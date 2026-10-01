# ADR-008: Our own agent loop; tools in-process until the MCP servers exist

- Status: Accepted
- Date: 2026-10-01

## Context
Agents must never move money or send messages (rule 5), never calculate money (rule 6), and
only see the user's own data (rule 7). They need hard limits (iterations, tokens, time,
proposals, calls per tool), structured tool errors, full transcripts and deterministic replay
(§16). Frameworks hide the loop and make these guarantees harder to test.

## Decision
- `kharcha_runtime` owns the loop: model call -> permission check -> tool -> repeat. LiteLLM
  is only a single-call adapter with a fallback chain and a per-model circuit breaker.
- `propose_message` is handled by the runtime: a proposal is accepted only after the content
  filter and the grounding check (numbers from this run's tool results); one rewrite, then drop.
  Any limit, timeout, error or a second denied tool ends the run with no proposals.
- The orchestrator (infrastructure, may use the DB) dedupes tasks, enforces a per-user daily
  run budget, stores transcripts in `agent_runs`, and always appends a deterministic templated
  nudge built from the same tools. With no LLM configured the product still works.
- The notifier's policy gate consumes `agent-results` and sends at most one message per result.
- Tools are plain user-scoped handlers (`kharcha_mcp_finance`, `kharcha_mcp_notify`) behind the
  `ToolExecutor` protocol and run in-process for now. W13 wraps the same handlers in MCP servers
  with per-task scoped tokens; the runtime does not change.
- The run context carries the clock, so tools, replays and tests are deterministic.

## Alternatives considered
- **LangGraph / agent frameworks**: faster start, but limits, grounding and replay would be
  bolted on around a loop we do not control.
- **MCP servers from day one**: the same handlers, but adds transport and auth work before the
  first agent exists. The executor protocol keeps the switch small.

## Consequences
- Coach v0 in the notifier is gone; the Coach runs in `kharcha-agents`.
- Agent code must not import database modules; only `orchestrator` (infra) does. Enforce with
  import-linter in W9.
