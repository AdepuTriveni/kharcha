"""Processor metrics (PROJECT_SPEC §29)."""

from prometheus_client import Counter

PREFILTER_DROPS = Counter(
    "kharcha_prefilter_drops_total", "Messages dropped by the pre-filter", ["reason"]
)
PARSE_METHOD = Counter("kharcha_parse_method_total", "Parsed transactions by method", ["method"])
PARSE_FAILURES = Counter(
    "kharcha_parse_failures_total", "Messages that failed parsing validation", ["reason"]
)
NOT_TRANSACTION = Counter(
    "kharcha_parse_not_transaction_total", "Messages a model judged not to be transactions"
)
TRANSACTIONS_WRITTEN = Counter(
    "kharcha_transactions_written_total", "Transactions inserted", ["kind"]
)
