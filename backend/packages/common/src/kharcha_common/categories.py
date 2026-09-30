"""Category taxonomy (PROJECT_SPEC §12)."""

from enum import StrEnum


class Category(StrEnum):
    FOOD_DELIVERY = "FOOD_DELIVERY"
    DINING_OUT = "DINING_OUT"
    QUICK_COMMERCE_SNACKS = "QUICK_COMMERCE_SNACKS"
    GROCERIES = "GROCERIES"
    SHOPPING = "SHOPPING"
    ENTERTAINMENT = "ENTERTAINMENT"
    SUBSCRIPTIONS = "SUBSCRIPTIONS"
    TRAVEL = "TRAVEL"
    TRANSPORT = "TRANSPORT"
    BILLS_UTILITIES = "BILLS_UTILITIES"
    RENT = "RENT"
    HEALTH = "HEALTH"
    EDUCATION = "EDUCATION"
    TRANSFERS = "TRANSFERS"
    CASH_UNCATEGORIZED = "CASH_UNCATEGORIZED"
    OTHER = "OTHER"


class Roastable(StrEnum):
    YES = "YES"
    NO = "NO"
    NEVER = "NEVER"
    EXTREME_SPIKES_ONLY = "EXTREME_SPIKES_ONLY"
    DUPLICATES_OR_UNUSED = "DUPLICATES_OR_UNUSED"
    GENTLY = "GENTLY"
    VANISHING_CASH_ONLY = "VANISHING_CASH_ONLY"


ESSENTIAL: frozenset[Category] = frozenset(
    {
        Category.GROCERIES,
        Category.TRANSPORT,
        Category.BILLS_UTILITIES,
        Category.RENT,
        Category.HEALTH,
        Category.EDUCATION,
    }
)

ROASTABLE: dict[Category, Roastable] = {
    Category.FOOD_DELIVERY: Roastable.YES,
    Category.DINING_OUT: Roastable.YES,
    Category.QUICK_COMMERCE_SNACKS: Roastable.YES,
    Category.GROCERIES: Roastable.EXTREME_SPIKES_ONLY,
    Category.SHOPPING: Roastable.YES,
    Category.ENTERTAINMENT: Roastable.YES,
    Category.SUBSCRIPTIONS: Roastable.DUPLICATES_OR_UNUSED,
    Category.TRAVEL: Roastable.GENTLY,
    Category.TRANSPORT: Roastable.NO,
    Category.BILLS_UTILITIES: Roastable.NEVER,
    Category.RENT: Roastable.NEVER,
    Category.HEALTH: Roastable.NEVER,
    Category.EDUCATION: Roastable.NEVER,
    Category.TRANSFERS: Roastable.NEVER,
    Category.CASH_UNCATEGORIZED: Roastable.VANISHING_CASH_ONLY,
    Category.OTHER: Roastable.NO,
}


def is_essential(category: Category) -> bool:
    return category in ESSENTIAL
