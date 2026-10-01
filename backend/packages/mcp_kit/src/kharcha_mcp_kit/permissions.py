"""Tool permission table (PROJECT_SPEC §17.3), enforced by every tool server.

The runtime checks the agent's allowlist too (``config/agents.yaml``); this server-side copy is
the second layer, so a compromised or buggy agent cannot reach a tool it is not granted.
"""

FINANCE_READ = frozenset(
    {
        "get_weekly_summary",
        "list_transactions",
        "get_cash_balance",
        "get_forecast",
        "what_if",
        "get_budgets",
    }
)
ANALYST_SQL = frozenset({"run_analyst_sql", "describe_schema", "make_chart"})
MEMORY_READ = frozenset({"search_memories", "list_commitments"})
MEMORY_WRITE = frozenset({"write_memory", "update_memory", "forget_memory"})
NOTIFY_READ = frozenset({"get_recent_alerts", "get_feedback_stats"})
REFUND = frozenset({"get_case", "save_draft"})
# §24: what a user's own assistant may call (read-only, no SQL).
EXTERNAL = frozenset(
    {
        "get_spending_summary",
        "list_transactions",
        "get_cash_balance",
        "get_broke_date_forecast",
        "list_refund_cases",
    }
)

TOOL_PERMISSIONS: dict[str, frozenset[str]] = {
    "coach": FINANCE_READ | MEMORY_READ | NOTIFY_READ | {"propose_message"},
    "cash_detective": FINANCE_READ | MEMORY_READ | NOTIFY_READ | {"propose_message"},
    "refund_advocate": frozenset({"list_transactions"})
    | NOTIFY_READ
    | REFUND
    | {"propose_message"},
    "memory_keeper": MEMORY_READ | MEMORY_WRITE | NOTIFY_READ,
    "analyst": FINANCE_READ | ANALYST_SQL | MEMORY_READ,
    "external": EXTERNAL,
}


def allowed(agent: str, tool: str) -> bool:
    return tool in TOOL_PERMISSIONS.get(agent, frozenset())
