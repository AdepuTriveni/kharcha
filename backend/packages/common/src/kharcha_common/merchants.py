"""Merchant normalization and seeds (PROJECT_SPEC §11.3).

``normalize_merchant`` turns raw payee text into the alias key used in ``merchant_aliases``.
"""

import re
from pathlib import Path

import yaml
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.categories import Category
from kharcha_common.db.models import Merchant, MerchantAlias

SEED_FILE = Path(__file__).resolve().parents[4] / "seeds" / "seed-merchants.yaml"

# Person VPAs are hashed on the phone as p<8 hex>@handle (android Redactor).
PERSON_VPA = re.compile(r"^p[0-9a-f]{8}@[a-z0-9]+$", re.I)
_STOPWORDS = frozenset(
    {"pvt", "private", "ltd", "limited", "llp", "inc", "india", "the", "co", "company", "and"}
)
_NON_WORD = re.compile(r"[^a-z&]+")


def is_person_vpa(raw: str | None) -> bool:
    return bool(raw and PERSON_VPA.match(raw.strip()))


def normalize_merchant(raw: str) -> str:
    """Alias key, e.g. ``"ZOMATO@hdfcbank"`` -> ``"zomato"``.

    ``"Rebel Foods Pvt Ltd"`` -> ``"rebel foods"``.
    """
    text = raw.strip().lower()
    if "@" in text:
        text = text.split("@", 1)[0]
    words = [w for w in _NON_WORD.split(text.replace("&", " & ")) if w and w not in _STOPWORDS]
    return " ".join(words).strip()


def load_seed_file(path: Path = SEED_FILE) -> dict[str, tuple[str, Category, list[str]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, tuple[str, Category, list[str]]] = {}
    for merchant_id, (name, category, aliases) in data.items():
        out[str(merchant_id)] = (str(name), Category(category), [str(a) for a in aliases])
    return out


async def seed_merchants(session: AsyncSession, path: Path = SEED_FILE) -> int:
    """Upsert seed merchants and their aliases. Returns the number of merchants."""
    seeds = load_seed_file(path)
    for merchant_id, (name, category, aliases) in seeds.items():
        await session.execute(
            insert(Merchant)
            .values(id=merchant_id, name=name, default_category=category.value)
            .on_conflict_do_update(
                index_elements=[Merchant.id],
                set_={"name": name, "default_category": category.value},
            )
        )
        for alias in {normalize_merchant(a) for a in [name, *aliases]} - {""}:
            await session.execute(
                insert(MerchantAlias)
                .values(alias=alias, merchant_id=merchant_id, source="SEED")
                .on_conflict_do_nothing()
            )
    return len(seeds)
