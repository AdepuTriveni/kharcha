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
uv run kharcha-admin create-user --name Me          # prints an API key + KHARCHA_API_KEYS line for .env
uv run kharcha-ingest                               # http://localhost:8000/healthz
uv run kharcha-admin seed-merchants                 # ~170 Indian merchants
uv run kharcha-processor                            # rules -> teacher parser, dedup, cash (metrics :8001)
uv run kharcha-insights                             # triggers + broke-date forecast (:8002)
uv run kharcha-notifier                             # Telegram bot, policy gate, schedules (:8003)
uv run kharcha-agents                               # orchestrator + Coach on the agent runtime (:8004)
uv run kharcha-eval parsing ../ml/data/samples/parsing_sample.jsonl --parser rules
uv run kharcha-eval forecast                        # synthetic backtest (±3-day accuracy, MAE)

cd ../ml && uv sync && uv run pytest
```

### Use your Kharcha data from your own AI assistant (MCP)

Create a personal, read-only token (90-day expiry, revocable) and point a desktop MCP client at
the external server. Example `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kharcha": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/kharcha/backend", "run", "kharcha-mcp-external",
               "--stdio", "--token", "kmcp_..."]
    }
  }
}
```

Get the token with `POST /v1/mcp-tokens` (or the app later); revoke with
`DELETE /v1/mcp-tokens/{id}`. Exposed tools: spending summary, transactions (max 100, no account
numbers), cash balance, broke-date forecast, refund cases. No SQL.

On Windows with Docker Desktop, set these before `uv run pytest -m integration` (PowerShell):

```powershell
$env:DOCKER_HOST = "npipe:////./pipe/dockerDesktopLinuxEngine"
$env:TESTCONTAINERS_RYUK_DISABLED = "true"   # Ryuk's port mapping is flaky on Docker Desktop
```

Local UIs: Kafka UI http://localhost:8081 · Temporal http://localhost:8233 ·
Prometheus http://localhost:9090 · Grafana http://localhost:3000.
