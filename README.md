# Kharcha

An AI-first money companion for Indian users. It captures bank and UPI notifications on
Android, turns them into clean transactions (including cash), and uses a team of AI agents
to roast unnecessary spending in Hinglish, predict your "broke date", and chase failed-payment
refunds.

- Specification: [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md)
- Progress: [docs/PROGRESS.md](docs/PROGRESS.md)
- Decisions: [docs/adr/](docs/adr/)

> Metrics (§32) and the competitor comparison will be added here in Phase 10.

## Quick start (local)

Needs Python 3.12, [uv](https://docs.astral.sh/uv/), Docker, and optionally
[Ollama](https://ollama.com) with a small model (`ollama pull qwen2.5:1.5b`).

```bash
docker compose -f infra/docker-compose.yml up -d    # Kafka, Postgres+pgvector, Redis, Temporal, Prometheus, Grafana

cd backend
uv sync
uv run alembic upgrade head
uv run pytest -m "not integration"                  # unit + contract tests
uv run pytest -m integration                        # migrations (testcontainers) + LLM smoke test
uv run kharcha-ingest                               # http://localhost:8000/healthz

cd ../ml && uv sync && uv run pytest
```

On Windows with Docker Desktop, set these before `uv run pytest -m integration` (PowerShell):

```powershell
$env:DOCKER_HOST = "npipe:////./pipe/dockerDesktopLinuxEngine"
$env:TESTCONTAINERS_RYUK_DISABLED = "true"   # Ryuk's port mapping is flaky on Docker Desktop
```

Local UIs: Kafka UI http://localhost:8081 · Temporal http://localhost:8233 ·
Prometheus http://localhost:9090 · Grafana http://localhost:3000.
