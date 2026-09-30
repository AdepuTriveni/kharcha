"""``POST /v1/telegram/link-code``: 6-digit code for ``/start <code>`` (PROJECT_SPEC §9, §25)."""

from fastapi import APIRouter, Request

from kharcha_common.events import CamelModel
from kharcha_common.linking import LINK_TTL_S, create_link_code
from kharcha_ingest.auth import CurrentUser

router = APIRouter(prefix="/v1")


class LinkCode(CamelModel):
    code: str
    expires_in_s: int


@router.post("/telegram/link-code")
async def link_code(user_id: CurrentUser, request: Request) -> LinkCode:
    code = await create_link_code(request.app.state.redis, user_id)
    return LinkCode(code=code, expires_in_s=LINK_TTL_S)
