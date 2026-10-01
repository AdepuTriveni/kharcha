"""mcp-refund tools (PROJECT_SPEC §17.3, §23). Facts come from code; the agent only words them.

``list_refund_cases`` is also exposed to the user's own assistants (§24).
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import RefundCase
from kharcha_common.money import format_inr
from kharcha_common.time import utcnow
from kharcha_runtime.tools import Tool, tool
from kharcha_runtime.types import RunContext


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CaseArgs(_Args):
    case_id: str = Field(alias="caseId")


class DraftArgs(_Args):
    case_id: str = Field(alias="caseId")
    text: str = Field(min_length=20, max_length=2000)


class NoArgs(_Args):
    pass


def facts(case: RefundCase) -> dict[str, Any]:
    return {
        "caseId": case.id,
        "caseType": case.case_type,
        "state": case.state,
        "amountPaise": case.amount_paise,
        "amountText": format_inr(case.amount_paise),
        "referenceId": case.reference_id,
        "openedAt": case.opened_at.isoformat(),
        "deadlineAt": case.deadline_at.isoformat(),
        "reversedAt": case.reversed_at.isoformat() if case.reversed_at else None,
        "compensationOwedPaise": case.compensation_owed_paise or 0,
        "compensationReceivedPaise": case.compensation_received_paise or 0,
        "hasDraft": bool(case.complaint_draft),
    }


async def _case(session: AsyncSession, user_id: str, case_id: str) -> RefundCase | None:
    row = await session.get(RefundCase, case_id)
    return row if row is not None and row.user_id == user_id else None


@tool("get_case", "Facts of one refund case (amount, reference, deadline, compensation).", CaseArgs)
async def get_case(session: AsyncSession, ctx: RunContext, args: CaseArgs) -> dict[str, Any]:
    case = await _case(session, ctx.user_id, args.case_id)
    return facts(case) if case else {"error": "NOT_FOUND", "message": "no such case"}


@tool(
    "save_draft", "Save complaint/escalation wording for the user to send (never sent).", DraftArgs
)
async def save_draft(session: AsyncSession, ctx: RunContext, args: DraftArgs) -> dict[str, Any]:
    case = await _case(session, ctx.user_id, args.case_id)
    if case is None:
        return {"error": "NOT_FOUND", "message": "no such case"}
    case.complaint_draft = args.text.strip()
    case.updated_at = utcnow()
    await session.commit()
    return {"caseId": case.id, "saved": True}


@tool("list_refund_cases", "The user's refund cases, newest first.", NoArgs)
async def list_refund_cases(session: AsyncSession, ctx: RunContext, args: NoArgs) -> dict[str, Any]:
    rows = (
        await session.scalars(
            select(RefundCase)
            .where(RefundCase.user_id == ctx.user_id)
            .order_by(RefundCase.opened_at.desc())
            .limit(50)
        )
    ).all()
    return {"cases": [facts(c) for c in rows]}


TOOLS: dict[str, Tool] = {t.spec.name: t for t in (get_case, save_draft, list_refund_cases)}
