"""Export raw events for labeling (PROJECT_SPEC §15.2) -- ``kharcha-admin export-labeling``.

Only users with ``ml_consent = true`` (rule 9). Texts were redacted on the phone; as a second
guard any run of 13+ digits (card or full account number) is masked again here. Each line is a
``kharcha_ml.dataset.labels.LabeledExample`` with ``source = "REAL"`` and ``reviewed = false``;
the proposed label comes from the transaction the event produced, or "not a transaction".
"""

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import RawEventRow, TransactionRow, TransactionSource, User
from kharcha_common.events import RawEventType
from kharcha_common.money import paise_to_rupees

EXPORTED_TYPES = (RawEventType.RAW_NOTIFICATION.value, RawEventType.RAW_SMS.value)
_LONG_DIGITS = re.compile(r"\d{13,}")


def scrub(text: str) -> str:
    return _LONG_DIGITS.sub(lambda m: "X" * (len(m.group()) - 4) + m.group()[-4:], text)


def _amount(paise: int | None) -> str | None:
    return None if paise is None else str(paise_to_rupees(paise))


def proposed_label(txn: TransactionRow | None) -> dict[str, Any]:
    if txn is None:
        return {"isTransaction": False}
    label: dict[str, Any] = {
        "isTransaction": True,
        "amount": _amount(txn.amount_paise),
        "direction": txn.direction,
        "channel": txn.channel,
        "status": txn.status,
        "merchantRaw": txn.merchant_raw,
        "referenceId": txn.reference_id,
        "accountHint": txn.account_hint,
        "balanceAfter": _amount(txn.balance_after_paise),
    }
    return {k: v for k, v in label.items() if v is not None}


def example(row: RawEventRow, txn: TransactionRow | None) -> dict[str, Any]:
    assert row.text is not None
    return {
        "eventId": str(row.event_id),
        "sender": row.sender,
        "sourceApp": row.source_app,
        "text": scrub(row.text),
        "source": "REAL",
        "label": proposed_label(txn),
        "reviewed": False,
    }


async def collect(session: AsyncSession) -> list[dict[str, Any]]:
    stmt = (
        select(RawEventRow, TransactionRow)
        .join(User, User.id == RawEventRow.user_id)
        .outerjoin(TransactionSource, TransactionSource.raw_event_id == RawEventRow.event_id)
        .outerjoin(TransactionRow, TransactionRow.id == TransactionSource.transaction_id)
        .where(
            User.ml_consent.is_(True),
            RawEventRow.type.in_(EXPORTED_TYPES),
            RawEventRow.text.is_not(None),
            # Scoped by user_id through the consent join; no user ids are written out.
            TransactionRow.user_id.is_(None) | (TransactionRow.user_id == RawEventRow.user_id),
        )
        .order_by(RawEventRow.posted_at, RawEventRow.event_id)
    )
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw, txn in (await session.execute(stmt)).all():
        if str(raw.event_id) in seen:
            continue
        seen.add(str(raw.event_id))
        out.append(example(raw, txn))
    return out


def write(path: Path, examples: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in examples:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return len(examples)
