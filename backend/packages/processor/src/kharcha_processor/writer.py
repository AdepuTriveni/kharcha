"""W2 transaction writer: parsed transaction -> ``transactions`` row -> clean-transactions.

Temporary stand-in for dedup + merchants + categories (W4), which will replace this stage.
Each parsed event becomes one transaction whose id is derived from the raw event id.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.categories import Category, is_essential
from kharcha_common.db.models import TransactionRow, TransactionSource
from kharcha_common.events import (
    Channel,
    CleanTransactionPayload,
    Direction,
    ParsedTransactionPayload,
    TxnKind,
    TxnStatus,
)
from kharcha_common.kafka import NAMESPACE_EVENTS


def transaction_id_for(raw_event_id: str) -> str:
    return "t_" + uuid.uuid5(NAMESPACE_EVENTS, f"txn/{raw_event_id}").hex


def infer_kind(parsed: ParsedTransactionPayload) -> TxnKind:
    if parsed.status is TxnStatus.REVERSED:
        return TxnKind.REVERSAL
    if parsed.channel is Channel.ATM and parsed.direction is Direction.DEBIT:
        return TxnKind.ATM_WITHDRAWAL
    if parsed.direction is Direction.CREDIT:
        return TxnKind.REFUND if parsed.status is TxnStatus.REFUND_INITIATED else TxnKind.INCOME
    return TxnKind.SPEND


async def write_transaction(
    session: AsyncSession, user_id: str, parsed: ParsedTransactionPayload
) -> TransactionRow:
    """Insert the transaction if new; return the stored row either way."""
    txn_id = transaction_id_for(parsed.raw_event_id)
    category = Category.OTHER
    values = {
        "id": txn_id,
        "user_id": user_id,
        "amount_paise": parsed.amount_paise,
        "direction": parsed.direction.value,
        "kind": infer_kind(parsed).value,
        "status": parsed.status.value,
        "channel": parsed.channel.value,
        "merchant_raw": parsed.merchant_raw,
        "category": category.value,
        "is_essential": is_essential(category),
        "reference_id": parsed.reference_id,
        "account_hint": parsed.account_hint,
        "balance_after_paise": parsed.balance_after_paise,
        "txn_time": parsed.txn_time,
        "parse_method": parsed.parse_method.value,
    }
    await session.execute(insert(TransactionRow).values(**values).on_conflict_do_nothing())
    await session.execute(
        insert(TransactionSource)
        .values(transaction_id=txn_id, raw_event_id=uuid.UUID(parsed.raw_event_id))
        .on_conflict_do_nothing()
    )
    row = (
        await session.execute(
            select(TransactionRow).where(
                TransactionRow.id == txn_id, TransactionRow.user_id == user_id
            )
        )
    ).scalar_one()
    return row


def clean_payload(row: TransactionRow, source_event_ids: list[str]) -> CleanTransactionPayload:
    return CleanTransactionPayload(
        transaction_id=row.id,
        amount_paise=row.amount_paise,
        direction=Direction(row.direction),
        kind=TxnKind(row.kind),
        status=TxnStatus(row.status),
        merchant_id=row.merchant_id,
        merchant_name=None,
        category=row.category,
        is_essential=row.is_essential,
        reference_id=row.reference_id,
        txn_time=row.txn_time,
        source_event_ids=source_event_ids,
        version=row.version,
    )
