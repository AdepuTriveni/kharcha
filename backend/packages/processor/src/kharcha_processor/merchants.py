"""Merchant resolution (PROJECT_SPEC §11.3): exact alias -> trigram >= 0.6 -> unknown.

Person VPAs map to TRANSFERS and are never matched to a merchant. Teacher suggestions
(source=LLM) and user corrections (source=USER) arrive in W5/W6.
"""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.categories import Category
from kharcha_common.db.models import Merchant, MerchantAlias
from kharcha_common.merchants import is_person_vpa, normalize_merchant

TRIGRAM_MATCH = 0.6


class MatchMethod(StrEnum):
    EXACT = "EXACT"
    TRIGRAM = "TRIGRAM"
    PERSON = "PERSON"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class MerchantMatch:
    merchant_id: str | None
    name: str | None
    category: Category
    method: MatchMethod


UNKNOWN = MerchantMatch(None, None, Category.OTHER, MatchMethod.NONE)
PERSON = MerchantMatch(None, None, Category.TRANSFERS, MatchMethod.PERSON)


async def resolve_merchant(session: AsyncSession, raw: str | None) -> MerchantMatch:
    if not raw:
        return UNKNOWN
    if is_person_vpa(raw):
        return PERSON
    key = normalize_merchant(raw)
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
