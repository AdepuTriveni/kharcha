"""processor.dedup (PROJECT_SPEC §11): parsed transaction -> new or merged ``transactions`` row.

Runs serially per user (Kafka key = user_id). Returns every row whose content changed so the
caller can republish ``clean-transactions`` with the new ``version``.

Matching (§11.1): candidates have the same amount and direction within 15 min (48 h when a
reference id is present). Same reference -> merge. Otherwise score: 0.4 amount+direction,
+0.3 if |dt| <= 3 min, +0.2 compatible merchant, +0.1 same account hint. Merge at >= 0.8;
0.6-0.8 only if the source types differ (SMS vs notification). Two notifications from the same
app are never merged without a shared reference.

Special cases (§11.2): credit with the reference of an earlier debit -> REVERSAL; credit from a
merchant paid in the last 90 days -> REFUND; debit + credit of the same amount between two of
the user's accounts within 10 min -> both SELF_TRANSFER.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.categories import Category, is_essential
from kharcha_common.db.models import RawEventRow, TransactionRow, TransactionSource
from kharcha_common.events import (
    Channel,
    Direction,
    ParsedTransactionPayload,
    TxnKind,
    TxnStatus,
)
from kharcha_common.ids import NAMESPACE_EVENTS
from kharcha_common.merchants import normalize_merchant
from kharcha_common.time import utcnow
from kharcha_processor.merchants import MerchantMatch, resolve_merchant

WINDOW = timedelta(minutes=15)
WINDOW_WITH_REF = timedelta(hours=48)
CLOSE_IN_TIME = timedelta(minutes=3)
SELF_TRANSFER_WINDOW = timedelta(minutes=10)
REFUND_LOOKBACK = timedelta(days=90)
MERGE_SCORE = 0.8
MAYBE_SCORE = 0.6
MERCHANT_SIMILARITY = 0.4


@dataclass(frozen=True, slots=True)
class SourceMeta:
    """Where a parsed transaction came from (raw event type and app)."""

    raw_type: str
    source_app: str | None


def transaction_id_for(raw_event_id: str) -> str:
    return "t_" + uuid.uuid5(NAMESPACE_EVENTS, f"txn/{raw_event_id}").hex


def base_kind(parsed: ParsedTransactionPayload) -> TxnKind:
    if parsed.status is TxnStatus.REVERSED:
        return TxnKind.REVERSAL
    if parsed.channel is Channel.ATM and parsed.direction is Direction.DEBIT:
        return TxnKind.ATM_WITHDRAWAL
    if parsed.direction is Direction.CREDIT:
        return TxnKind.REFUND if parsed.status is TxnStatus.REFUND_INITIATED else TxnKind.INCOME
    return TxnKind.SPEND


def _trigrams(text: str) -> set[str]:
    padded = f"  {text} "
    return {padded[i : i + 3] for i in range(len(text) + 1)}


def merchant_similarity(a: str | None, b: str | None) -> float:
    """Cheap trigram Jaccard on normalized names (same VPA -> 1.0)."""
    if not a or not b:
        return 0.0
    if a.strip().lower() == b.strip().lower():
        return 1.0
    na, nb = normalize_merchant(a), normalize_merchant(b)
    if not na or not nb:
        return 0.0
    ga, gb = _trigrams(na), _trigrams(nb)
    return len(ga & gb) / len(ga | gb)


def shares_reference(parsed: ParsedTransactionPayload, candidate: TransactionRow) -> bool:
    return bool(parsed.reference_id) and candidate.reference_id == parsed.reference_id


def match_score(parsed: ParsedTransactionPayload, candidate: TransactionRow) -> float:
    """§11.1 score (max 1.0). Assumes amount + direction already equal."""
    score = 0.4
    if abs(parsed.txn_time - candidate.txn_time) <= CLOSE_IN_TIME:
        score += 0.3
    if merchant_similarity(parsed.merchant_raw, candidate.merchant_raw) >= MERCHANT_SIMILARITY:
        score += 0.2
    if parsed.account_hint and candidate.account_hint == parsed.account_hint:
        score += 0.1
    return round(score, 2)


def should_merge(
    score: float, new: SourceMeta, existing: list[SourceMeta], *, same_reference: bool = False
) -> bool:
    if same_reference:  # definite merge
        return True
    same_app_notification = any(
        e.raw_type == new.raw_type == "RAW_NOTIFICATION" and e.source_app == new.source_app
        for e in existing
    )
    if same_app_notification:
        return False
    if score >= MERGE_SCORE:
        return True
    return score >= MAYBE_SCORE and any(e.raw_type != new.raw_type for e in existing)


async def _sources(session: AsyncSession, transaction_id: str) -> list[SourceMeta]:
    rows = await session.execute(
        select(RawEventRow.type, RawEventRow.source_app)
        .join(TransactionSource, TransactionSource.raw_event_id == RawEventRow.event_id)
        .where(TransactionSource.transaction_id == transaction_id)
    )
    return [SourceMeta(t, app) for t, app in rows.all()]


async def _candidates(
    session: AsyncSession, user_id: str, parsed: ParsedTransactionPayload
) -> list[TransactionRow]:
    window = WINDOW_WITH_REF if parsed.reference_id else WINDOW
    rows = await session.execute(
        select(TransactionRow)
        .where(
            TransactionRow.user_id == user_id,
            TransactionRow.amount_paise == parsed.amount_paise,
            TransactionRow.direction == parsed.direction.value,
            TransactionRow.txn_time >= parsed.txn_time - window,
            TransactionRow.txn_time <= parsed.txn_time + window,
        )
        .order_by(func.abs(func.extract("epoch", TransactionRow.txn_time - parsed.txn_time)))
    )
    return list(rows.scalars())


def _richer(row: TransactionRow, parsed: ParsedTransactionPayload, match: MerchantMatch) -> bool:
    """Fill missing fields on ``row`` from ``parsed``. Returns True if anything changed."""
    changed = False
    fills = {
        "merchant_raw": parsed.merchant_raw,
        "reference_id": parsed.reference_id,
        "account_hint": parsed.account_hint,
        "balance_after_paise": parsed.balance_after_paise,
    }
    for name, value in fills.items():
        if value is not None and getattr(row, name) is None:
            setattr(row, name, value)
            changed = True
    if row.merchant_id is None and match.merchant_id is not None and not row.user_corrected:
        row.merchant_id = match.merchant_id
        row.category = match.category.value
        row.is_essential = is_essential(match.category)
        changed = True
    if row.status == TxnStatus.PENDING.value and parsed.status is not TxnStatus.PENDING:
        row.status = parsed.status.value
        changed = True
    return changed


async def _special_kind(
    session: AsyncSession, user_id: str, parsed: ParsedTransactionPayload, match: MerchantMatch
) -> tuple[TxnKind, TransactionRow | None]:
    """Kind for a new transaction, and an existing row that must also change (self-transfer)."""
    kind = base_kind(parsed)
    if parsed.direction is not Direction.CREDIT:
        partner_direction = Direction.CREDIT
    else:
        partner_direction = Direction.DEBIT
        if parsed.reference_id:
            reversed_debit = (
                await session.execute(
                    select(TransactionRow.id).where(
                        TransactionRow.user_id == user_id,
                        TransactionRow.direction == Direction.DEBIT.value,
                        TransactionRow.reference_id == parsed.reference_id,
                    )
                )
            ).first()
            if reversed_debit is not None:
                return TxnKind.REVERSAL, None
        if match.merchant_id is not None:
            paid_before = (
                await session.execute(
                    select(TransactionRow.id).where(
                        TransactionRow.user_id == user_id,
                        TransactionRow.direction == Direction.DEBIT.value,
                        TransactionRow.merchant_id == match.merchant_id,
                        TransactionRow.txn_time >= parsed.txn_time - REFUND_LOOKBACK,
                        TransactionRow.txn_time <= parsed.txn_time,
                    )
                )
            ).first()
            if paid_before is not None:
                return TxnKind.REFUND, None

    if parsed.account_hint and kind in {TxnKind.SPEND, TxnKind.INCOME}:
        partner = (
            (
                await session.execute(
                    select(TransactionRow).where(
                        TransactionRow.user_id == user_id,
                        TransactionRow.amount_paise == parsed.amount_paise,
                        TransactionRow.direction == partner_direction.value,
                        TransactionRow.kind.in_([TxnKind.SPEND.value, TxnKind.INCOME.value]),
                        TransactionRow.account_hint.is_not(None),
                        TransactionRow.account_hint != parsed.account_hint,
                        TransactionRow.txn_time >= parsed.txn_time - SELF_TRANSFER_WINDOW,
                        TransactionRow.txn_time <= parsed.txn_time + SELF_TRANSFER_WINDOW,
                    )
                )
            )
            .scalars()
            .first()
        )
        if partner is not None:
            return TxnKind.SELF_TRANSFER, partner
    return kind, None


def _mark_self_transfer(row: TransactionRow) -> None:
    row.kind = TxnKind.SELF_TRANSFER.value
    row.category = Category.TRANSFERS.value
    row.is_essential = False


async def apply_parsed(
    session: AsyncSession, user_id: str, parsed: ParsedTransactionPayload
) -> list[TransactionRow]:
    """Insert or merge ``parsed``. Returns rows that are new or changed (versions bumped)."""
    raw_id = uuid.UUID(parsed.raw_event_id)
    already = (
        await session.execute(
            select(TransactionSource.transaction_id).where(TransactionSource.raw_event_id == raw_id)
        )
    ).scalar_one_or_none()
    if already is not None:  # this raw event is already part of a transaction
        row = await session.get(TransactionRow, already)
        return [row] if row is not None and row.user_id == user_id else []

    raw = (
        await session.execute(
            select(RawEventRow.type, RawEventRow.source_app).where(
                RawEventRow.event_id == raw_id, RawEventRow.user_id == user_id
            )
        )
    ).one()
    meta = SourceMeta(raw.type, raw.source_app)
    match = await resolve_merchant(session, parsed.merchant_raw, user_id)
    now = utcnow()

    for candidate in await _candidates(session, user_id, parsed):
        score = match_score(parsed, candidate)
        sources = await _sources(session, candidate.id)
        if should_merge(score, meta, sources, same_reference=shares_reference(parsed, candidate)):
            _richer(candidate, parsed, match)
            candidate.version += 1  # a new source always bumps the version
            candidate.updated_at = now
            await session.execute(
                insert(TransactionSource)
                .values(transaction_id=candidate.id, raw_event_id=raw_id)
                .on_conflict_do_nothing()
            )
            await session.flush()
            return [candidate]

    kind, partner = await _special_kind(session, user_id, parsed, match)
    category = match.category
    if kind in {TxnKind.SELF_TRANSFER, TxnKind.REVERSAL}:
        category = Category.TRANSFERS if kind is TxnKind.SELF_TRANSFER else match.category
    row = TransactionRow(
        id=transaction_id_for(parsed.raw_event_id),
        user_id=user_id,
        amount_paise=parsed.amount_paise,
        direction=parsed.direction.value,
        kind=kind.value,
        status=parsed.status.value,
        channel=parsed.channel.value,
        merchant_id=match.merchant_id,
        merchant_raw=parsed.merchant_raw,
        category=category.value,
        is_essential=is_essential(category),
        reference_id=parsed.reference_id,
        account_hint=parsed.account_hint,
        balance_after_paise=parsed.balance_after_paise,
        txn_time=parsed.txn_time,
        parse_method=parsed.parse_method.value,
        user_corrected=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    await session.execute(
        insert(TransactionSource)
        .values(transaction_id=row.id, raw_event_id=raw_id)
        .on_conflict_do_nothing()
    )
    changed = [row]
    if partner is not None:
        _mark_self_transfer(partner)
        partner.version += 1
        partner.updated_at = now
        changed.append(partner)
    await session.flush()
    return changed
