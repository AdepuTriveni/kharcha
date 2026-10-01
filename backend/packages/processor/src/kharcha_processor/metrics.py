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
RULE_OBSERVATIONS = Counter(
    "kharcha_rule_observations_total", "Rule outputs compared with another tier", ["outcome"]
)
RULE_STATUS_CHANGES = Counter(
    "kharcha_rule_status_changes_total", "Rules promoted or disabled", ["status"]
)
RULES_SYNTHESIZED = Counter(
    "kharcha_rules_synthesized_total", "Rule synthesis attempts by result", ["result"]
)
SHADOW_COMPARISONS = Counter(
    "kharcha_parser_shadow_total", "Shadow comparisons between tiers", ["agreement"]
)
