"""Transaction read helpers shared by the processor and the API (PROJECT_SPEC §7.3, §9)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import Merchant, TransactionRow, TransactionSource
from kharcha_common.events import CleanTransactionPayload, Direction, TxnKind, TxnStatus


async def merchant_name(session: AsyncSession, merchant_id: str | None) -> str | None:
    if merchant_id is None:
        return None
    return (
        await session.execute(select(Merchant.name).where(Merchant.id == merchant_id))
    ).scalar_one_or_none()


async def clean_payload(session: AsyncSession, row: TransactionRow) -> CleanTransactionPayload:
    sources = (
        await session.execute(
            select(TransactionSource.raw_event_id).where(TransactionSource.transaction_id == row.id)
        )
    ).scalars()
    return CleanTransactionPayload(
        transaction_id=row.id,
        amount_paise=row.amount_paise,
        direction=Direction(row.direction),
        kind=TxnKind(row.kind),
        status=TxnStatus(row.status),
        merchant_id=row.merchant_id,
        merchant_name=await merchant_name(session, row.merchant_id),
        category=row.category,
        is_essential=row.is_essential,
        reference_id=row.reference_id,
        txn_time=row.txn_time,
        source_event_ids=sorted(str(s) for s in sources),
        version=row.version,
    )
