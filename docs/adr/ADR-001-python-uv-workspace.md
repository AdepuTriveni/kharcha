# ADR-001: All-Python backend as a uv workspace of small services

- Status: Accepted
- Date: 2026-09-30

## Context
Kharcha has a solo builder and many backend concerns: an HTTP API, a Kafka pipeline, agents,
MCP servers, Temporal workflows and ML inference. Each should deploy and scale on its own, but
the project cannot afford several languages, build systems and dependency sets.

## Decision
Build the whole backend in Python 3.12 as one uv workspace (`backend/`). Each service is a
package under `backend/packages/` with its own console entry point and Kafka consumer group,
and each ships as its own process and image. Shared code (events, topics, money, time,
settings, DB base) lives in `kharcha_common`. The workspace root is a non-packaged project
that owns Alembic migrations and cross-package tests. A single `uv.lock` pins every version.

## Alternatives considered
- **JVM (Kotlin/Spring) pipeline with Python only for AI**: strong Kafka tooling, but two
  ecosystems, duplicated event models and a heavier local stack.
- **One Python monolith**: simpler to deploy, but no independent scaling or failure isolation
  between the API, the pipeline and the agents.
- **A separate repo or venv per service**: isolation, but versions drift and shared code would
  have to be published and bumped.

## Consequences
- One language, one lockfile and one set of lint/type/test commands (ruff, mypy --strict,
  pytest) for everything.
- Package boundaries must be enforced in code: import-linter contracts arrive in W9, and
  agents may not import DB code.
- Python throughput limits Kafka consumers, so we scale out through partitions and replicas
  (see the W22 load test).
- Heavy ML training dependencies are kept out of this workspace (ADR-014).
