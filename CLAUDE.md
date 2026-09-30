# CLAUDE.md — Kharcha

> Project memory for Claude Code. Keep this file short. The full specification is
> `docs/PROJECT_SPEC.md` (numbered sections, e.g. §15). Progress: `docs/PROGRESS.md`.

## What this project is
Kharcha is an AI-first money companion for Indian users. An Android app captures bank/UPI
notifications (and SMS in a sideload build) in the background; a Python + Kafka backend turns
them into clean transactions (including cash); and a team of AI agents roasts unnecessary
spending in Hinglish, predicts the user's "broke date", nudges before risky moments, answers
questions about the user's money, remembers commitments, and chases failed-payment refunds
under RBI rules. Parsing uses **our own fine-tuned small model**, which can run on-device.

## Repo map
```
android-app/         Kotlin + Compose: capture, redaction, on-device model, cash entry, widget
backend/             uv workspace (Python 3.12); packages/ each = one service
  packages/common/        kharcha_common: Pydantic events, topics, money, time, DB models, settings
  packages/ingest_api/    FastAPI (phone uploads, Ask, bill photos, settings)
  packages/processor/     parsing (rules → own model → teacher), dedup, merchants, cash
  packages/insights/      forecast, risk scoring (onnxruntime), bandit, triggers
  packages/agent_runtime/ agent loop, limits, tool permissions, grounding, transcripts, replay
  packages/agents/        orchestrator + Coach, Cash Detective, Refund Advocate, Memory Keeper, Analyst; kharcha-eval CLI
  packages/mcp_*/         MCP servers: finance, memory, notify, refund
  packages/notifier/      policy gate, Telegram, FCM, feedback
  packages/refund_worker/ Temporal workflows
  migrations/             Alembic
  prompts/  seeds/  schemas/  tests/
ml/                  separate uv project: datasets, distillation, fine-tuning, evals, GGUF/ONNX export
infra/               docker-compose, k8s, Terraform
tools/               labeling CLI, Locust load tests, chaos scripts
docs/                PROJECT_SPEC.md, PROGRESS.md, adr/
```

## Commands
```bash
docker compose -f infra/docker-compose.yml up -d   # Kafka, Postgres(+pgvector), Redis, Temporal, Prometheus, Grafana

cd backend
uv sync                                  # install workspace
uv run ruff check . && uv run ruff format --check .
uv run mypy packages                     # strict type checking
uv run pytest -m "not integration"       # unit tests
uv run pytest -m integration             # testcontainers-python (needs Docker)
uv run alembic upgrade head              # DB migrations
uv run kharcha-ingest                    # run a service (each package has an entry point)
uv run kharcha-eval parsing|ask|agents|forecast
uv run python -m kharcha_common.schemas export   # regenerate JSON Schemas for Android

cd ml && uv sync && uv run pytest
cd android-app && ./gradlew assembleSideloadDebug
```

## Non-negotiable rules
1. **Money is `int` paise** (Python) and `Long` paise (Kotlin). Never `float`. Use `Decimal`
   only for display conversion. NumPy arrays of money must be `int64` and summed as Python int.
2. **Time: store UTC-aware `datetime`, display IST (`Asia/Kolkata`).** No naive datetimes.
3. **Every Kafka consumer is idempotent** (`processed_events`); commit offsets only after the DB
   transaction commits. Kafka key = `user_id`.
4. **Never log raw message text or full account numbers.** Log `event_id`.
5. **AI never moves money, never submits anything to banks/regulators, never sends a message
   directly.** Agents only *propose*; the notifier's policy gate decides.
6. **AI never calculates money.** Numbers in AI text must come from tool results; the grounding
   check (§27.4) rejects any number not present in tool outputs.
7. **Every data access is scoped by `user_id`** from the auth token — HTTP, SQL (RLS), MCP.
8. **Gold evaluation sets are never used for training.** Split by sender template (§15.3).
9. **Training data only from consenting users** (`users.ml_consent = true`), redacted.
10. Schema changes via Alembic; event model changes bump `schema_version`, re-export JSON Schemas,
    and update §7.
11. No secrets in git.

## Conventions
- Python 3.12, fully async (FastAPI, FastStream, SQLAlchemy async, httpx). Type hints everywhere;
  `mypy --strict` must pass. Pydantic v2 models for all events, API bodies, and LLM outputs.
- Package names `kharcha_<name>`; each service has a `main.py` + console-script entry point.
- Settings via pydantic-settings; profiles `local`, `test`, `prod` through `KHARCHA_ENV`.
- Tests: pytest + pytest-asyncio; integration tests marked `@pytest.mark.integration`;
  property tests with Hypothesis for parsing and money logic.
- Agents may not import database code directly — they use MCP tools (enforced by import-linter).
- Prompts are versioned files in `backend/prompts/<agent>/vN.md`.
- Kotlin app: MVVM, Hilt, Room, WorkManager, Coroutines/Flow.
- Significant decisions → ADR in `docs/adr/`.

## How to work
- Read the matching §section before implementing. Follow `docs/PROGRESS.md` in order.
- Show a short plan before writing code for any task larger than one file.
- Keep `main` green; small commits (`feat(agents): ...`).
- If the spec is ambiguous or seems wrong, stop and ask.
