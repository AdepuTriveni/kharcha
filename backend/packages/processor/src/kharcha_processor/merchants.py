"""Merchant resolution (PROJECT_SPEC §11.3): exact alias -> trigram >= 0.6 -> unknown.

A user's own correction (``user_merchant_overrides``) wins for that user, before anything
else. Person VPAs map to TRANSFERS and are never matched to a merchant. Teacher suggestions
(source=LLM) are not used yet.
"""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.categories import Category
from kharcha_common.db.models import Merchant, MerchantAlias, UserMerchantOverride
from kharcha_common.merchants import is_person_vpa, normalize_merchant

TRIGRAM_MATCH = 0.6


class MatchMethod(StrEnum):
    EXACT = "EXACT"
    TRIGRAM = "TRIGRAM"
    PERSON = "PERSON"
    USER = "USER"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class MerchantMatch:
    merchant_id: str | None
    name: str | None
    category: Category
    method: MatchMethod


UNKNOWN = MerchantMatch(None, None, Category.OTHER, MatchMethod.NONE)
PERSON = MerchantMatch(None, None, Category.TRANSFERS, MatchMethod.PERSON)


async def _user_override(session: AsyncSession, user_id: str, key: str) -> MerchantMatch | None:
    row = (
        await session.execute(
            select(UserMerchantOverride, Merchant.name)
            .outerjoin(Merchant, Merchant.id == UserMerchantOverride.merchant_id)
            .where(UserMerchantOverride.user_id == user_id, UserMerchantOverride.alias == key)
        )
    ).first()
    if row is None:
        return None
    override, name = row
    return MerchantMatch(override.merchant_id, name, Category(override.category), MatchMethod.USER)


async def resolve_merchant(
    session: AsyncSession, raw: str | None, user_id: str | None = None
) -> MerchantMatch:
    if not raw:
        return UNKNOWN
    key = normalize_merchant(raw)
    if key and user_id is not None and (override := await _user_override(session, user_id, key)):
        return override
    if is_person_vpa(raw):
        return PERSON
    if not key:
        return UNKNOWN

    exact = (
        await session.execute(
            select(Merchant)
            .join(MerchantAlias, MerchantAlias.merchant_id == Merchant.id)
            .where(MerchantAlias.alias == key)
        )
    ).scalar_one_or_none()
    if exact is not None:
        return MerchantMatch(
            exact.id, exact.name, Category(exact.default_category), MatchMethod.EXACT
        )

    score = func.similarity(MerchantAlias.alias, key)
    best = (
        await session.execute(
            select(Merchant, score)
            .join(MerchantAlias, MerchantAlias.merchant_id == Merchant.id)
            .where(MerchantAlias.alias.op("%")(key))
            .order_by(score.desc())
            .limit(1)
        )
    ).first()
    if best is not None and float(best[1]) >= TRIGRAM_MATCH:
        merchant = best[0]
        return MerchantMatch(
            merchant.id, merchant.name, Category(merchant.default_category), MatchMethod.TRIGRAM
        )
    return UNKNOWN
