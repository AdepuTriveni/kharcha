from datetime import UTC, datetime, timedelta

import pytest

from kharcha_common.categories import Category
from kharcha_common.db.models import TransactionRow
from kharcha_common.events import (
    Channel,
    Direction,
    ParsedTransactionPayload,
    ParseMethod,
    TxnStatus,
)
from kharcha_common.merchants import is_person_vpa, load_seed_file, normalize_merchant
from kharcha_processor.dedup import (
    SourceMeta,
    match_score,
    merchant_similarity,
    shares_reference,
    should_merge,
)

T0 = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
NOTIF_PHONEPE = SourceMeta("RAW_NOTIFICATION", "com.phonepe.app")
NOTIF_BANK = SourceMeta("RAW_NOTIFICATION", "com.snapwork.hdfc")
SMS = SourceMeta("RAW_SMS", None)


def _parsed(**kw: object) -> ParsedTransactionPayload:
    base: dict[str, object] = {
        "raw_event_id": "0190f3a2-7c1e-7b3a-9d2e-4b1f6a0c9e11",
        "amount_paise": 34900,
        "direction": Direction.DEBIT,
        "channel": Channel.UPI,
        "status": TxnStatus.SUCCESS,
        "merchant_raw": "zomato@hdfcbank",
        "reference_id": None,
        "account_hint": "1234",
        "txn_time": T0,
        "parse_method": ParseMethod.TEACHER_LLM,
        "confidence": 0.9,
    }
    base.update(kw)
    return ParsedTransactionPayload.model_validate(base)


def _row(**kw: object) -> TransactionRow:
    base: dict[str, object] = {
        "merchant_raw": "ZOMATO",
        "reference_id": None,
        "account_hint": "1234",
        "txn_time": T0,
    }
    base.update(kw)
    return TransactionRow(**base)


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("zomato@hdfcbank", "zomato"),
        ("Bundl Technologies Pvt Ltd", "bundl technologies"),
        ("SWIGGY.STORES@icici", "swiggy stores"),
        ("H&M India", "h & m"),
        ("Amazon Pay India Private Limited", "amazon pay"),
        ("12345@ybl", ""),
    ],
)
def test_normalize_merchant(raw: str, key: str) -> None:
    assert normalize_merchant(raw) == key


def test_person_vpa() -> None:
    assert is_person_vpa("p3fa2c1d0@okaxis")
    assert not is_person_vpa("zomato@hdfcbank")


def test_seed_file_is_valid() -> None:
    seeds = load_seed_file()
    assert len(seeds) >= 150
    assert seeds["m_zomato"][1] is Category.FOOD_DELIVERY
    keys = [normalize_merchant(a) for _, _, aliases in seeds.values() for a in aliases]
    assert all(keys), "every alias must survive normalization"


def test_merchant_similarity() -> None:
    assert merchant_similarity("zomato@hdfcbank", "zomato@hdfcbank") == 1.0
    assert merchant_similarity("zomato@hdfcbank", "ZOMATO") == 1.0
    assert merchant_similarity("Zomato Ltd", "SWIGGY") < 0.4


def test_shared_reference() -> None:
    later = _row(reference_id="412345678901", txn_time=T0 + timedelta(hours=30))
    assert shares_reference(_parsed(reference_id="412345678901"), later)
    assert not shares_reference(_parsed(), _row())


def test_score_components() -> None:
    assert match_score(_parsed(), _row()) == 1.0  # 0.4 + 0.3 + 0.2 + 0.1
    assert match_score(_parsed(), _row(txn_time=T0 + timedelta(minutes=10))) == 0.7
    assert (
        match_score(
            _parsed(account_hint=None),
            _row(merchant_raw="Swiggy", txn_time=T0 + timedelta(minutes=10)),
        )
        == 0.4
    )


def test_should_merge_rules() -> None:
    assert should_merge(0.9, SMS, [NOTIF_PHONEPE])
    assert should_merge(0.7, SMS, [NOTIF_PHONEPE])  # 0.6-0.8 with different source types
    assert not should_merge(0.7, NOTIF_BANK, [NOTIF_PHONEPE])  # same type, below 0.8
    assert should_merge(0.9, NOTIF_BANK, [NOTIF_PHONEPE])  # bank app + UPI app
    assert not should_merge(0.9, NOTIF_PHONEPE, [NOTIF_PHONEPE])  # two payments, same app
    assert not should_merge(1.0, NOTIF_PHONEPE, [NOTIF_PHONEPE])  # perfect score, same app
    assert should_merge(0.4, NOTIF_PHONEPE, [NOTIF_PHONEPE], same_reference=True)
    assert not should_merge(0.5, SMS, [NOTIF_PHONEPE])
