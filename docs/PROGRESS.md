# Kharcha — Progress Tracker (v3, all-Python backend)

> Tick a box only when the "Done when" criteria in `PROJECT_SPEC.md` §34 pass.
> Claude Code: update this file after each task; record deferred items at the bottom.

## Prerequisites (§33.1)
- [ ] Laptop tools installed (Python 3.12 + uv, Docker, Android Studio, Ollama, Git, Claude Code)
- [ ] Accounts: GitHub, Telegram bot, Firebase, Kaggle/Colab, cloud free tier, free LLM API key, Langfuse
- [ ] Test phone ready (Android 10+)

## Phase 0 — Setup
- [x] Repo skeleton + uv workspace (common, ingest_api)
- [x] docker-compose + topic init script
- [x] Alembic 0001_core
- [x] CI: ruff, mypy, pytest (backend + ml), Android build (green on GitHub, run #2)
- [ ] Ollama smoke test via LiteLLM
- [x] `ml/` separate uv project skeleton

## Phase 1 — Usable by me
- [ ] W1 Capture + basic redaction + Room outbox + list screen
  - [x] code written (capture service, SMS receiver in sideload, redactor + corpus, outbox, uploader, screens)
  - [x] both flavors build; 13 unit tests green (redaction corpus, uploader, contract)
  - [ ] test on a phone (₹10 UPI payment appears redacted)
- [ ] W2 Upload API + raw-events + teacher-LLM parser + validation + IT test
  - [x] backend: `/v1/events:batch`, pre-filter, teacher LLM, §10.2 validation, DLT, transactions, e2e IT
  - [ ] Android WorkManager uploader (with W1)
  - [ ] "real payment -> correct row" on a phone
- [ ] W3 Telegram bot, daily summary, first trigger, chat cash entry
  - [x] code: notifier (bot, link codes, /summary /cash /undo /level /budget, cash confirm + Undo,
        daily summary, policy gate v0, coach v0), insights triggers, processor cash parser + ledger
  - [x] unit tests (cash parser, grounding, policy, texts)
  - [x] integration tests (bot flows, triggers -> coach, daily summary once); §7 + ADR-010
  - [ ] live test with a real bot token
- [ ] ✅ Using it daily

## Phase 2 — Correct data
- [ ] W4 Dedup, merchants, categories, labeling CLI, first parsing eval
  - [x] dedup + special cases, ~170 seed merchants, categories (unit + integration tests)
  - [x] labeling CLI (`kharcha-label`), template signatures, metrics, synthetic sample set
  - [x] `kharcha-admin export-labeling` (consent-only, re-scrubbed) + `kharcha-eval parsing` (JSON/MD + eval_runs)
  - [ ] first real parsing report: needs `ollama pull qwen2.5:1.5b` and some labeled messages
- [ ] W5 Rules + synthesis + shadow + DLTs + metrics
  - [x] rule engine + compile guard, 14 seed rules (auto-seeded at processor start), tiered parser
  - [x] synthesis (same-user samples, validated, CANDIDATE), shadow 10% -> `model-shadow`,
        promote after 5 / auto-disable > 2 mismatches, metrics, ADR-015, unit + integration tests
  - [x] `kharcha-eval parsing --parser rules|teacher|tiered`
  - [ ] "≥ 60% of my events parsed by rules": check `kharcha_parse_method_total` after a week of real use
- [ ] W6 Cash wallet, quick-add, widget, redaction corpus, corrections
  - [x] backend: ATM debit -> cash-events, WIDGET_TAP -> cash parser, `POST/DELETE /v1/cash`,
        `GET /v1/cash/balance`, `GET/PATCH /v1/transactions` (corrections -> per-user overrides,
        Alembic 0003_corrections), integration tests
  - [x] Android: Cash tab (balance, quick-add, presets, Undo), Payments tab (list + tap to correct),
        Glance widget presets (WIDGET_TAP), redaction corpus 9 -> 32 cases (Txn/Order ID kept),
        categories + presets contract fixtures checked on both sides; 25 unit tests
  - [ ] on a phone: widget tap -> cash balance; correction sticks for the next payment

## Phase 3 — Forecast, harness, friends
- [x] W7 Forecast + what-if + backtest
  - [x] Monte Carlo + recurring detection + what-if (pure, Hypothesis), `GET /v1/forecast`,
        BROKE_DATE_MOVED trigger, `kharcha-eval forecast` (synthetic: 67.8% ±3 d, MAE 4.9 d)
  - [x] Android Home tab: broke-date range card, "what if I cut…" chips + slider, one-time balance ask
  - [ ] real backtest once there are 60+ days of my own history
- [x] W8 Agent runtime (limits, permissions, transcripts, grounding, replay) + Coach v1 + policy gate
  - [x] `kharcha_runtime` loop + limits + DENIED_TOOL + grounding + content filter + replay + LiteLLM
        fallback/circuit breaker; finance + notify tools (in-process, user-scoped); orchestrator
        (`kharcha-agents`) with agent_runs transcripts + templated fallback; notifier policy gate on
        `agent-results`; Sunday 11:00 weekly review; ADR-008; unit + integration tests
  - [ ] with Ollama: check real Coach messages (`ollama pull qwen2.5:1.5b`, `kharcha-agents`)
- [ ] W9 Firebase auth + import-linter contracts + early cloud deploy + friends onboarded with consent
  - [x] Firebase ID-token verification (PyJWT + Google certs, same checks as firebase-admin), user
        created on first sign-in; API keys still work
  - [x] import-linter: 4 contracts in CI (agents/runtime never touch the DB, common is the bottom layer,
        services independent, tool servers never import agents)
  - [x] `GET/PUT /v1/settings` (roast level, quiet hours, budgets, ML consent + experiment opt-in),
        `DELETE /v1/me`; Android consent switches + delete-my-data
  - [x] backend Dockerfile + `infra/docker-compose.services.yml`; whole stack verified in Docker
        (SMS upload -> RULE parse -> Zomato/FOOD_DELIVERY)
  - [ ] YOU: create a Firebase project (Auth: phone/Google), add `google-services.json` to the app,
        set `KHARCHA_FIREBASE_PROJECT_ID`; then I wire the Android sign-in screen
  - [ ] YOU: a cloud VM (Oracle/AWS free tier) to run the compose stack; onboard friends

## Phase 4 — Own model (Pillar 1)
- [x] W10 Synthetic generator, distillation, review queue, template splits, dataset card, leakage test
  - [x] 28 hand-written templates (19 banks/apps) + 9 hard-negative kinds, exact labels, noise;
        Ollama teacher distillation + review queue (disagreements, failures, 10% sample);
        template splits with held-out banks (CANBNK, UBOI) in gold only, MinHash near-dup removal,
        `kharcha-dataset synth|distill|build`, `ml/data/DATASET_CARD.md`; leakage test in pytest
  - [ ] real data: export (consent) -> distill -> review queue with `kharcha-label` -> rebuild
- [ ] W11 Base-model comparison, LoRA fine-tune, five-way eval report
- [ ] W12 GGUF + quantization, Ollama tier 3, Android llama.cpp + constrained decoding, shadow rollout
- [ ] ✅ Model passes promotion gate and runs on phone

## Phase 5 — Multi-agent + MCP + memory (Pillars 2 & 5)
- [ ] W13 Four MCP servers + scoped tokens + permission tests; Coach on MCP; external MCP tokens
- [ ] W14 agent-tasks/results, orchestrator, Cash Detective, Memory Keeper skeleton
- [ ] W15 pgvector memory read/write, memory UI/commands, commitments, memory eval

## Phase 6 — Ask Kharcha + multimodal (Pillars 3 & 6)
- [ ] W16 Analyst views + RLS, SQL validator, tools, charts, /v1/ask, Telegram /ask, app chat
- [ ] W17 120-question eval + improvement loop; bill photo; Hinglish voice + evals

## Phase 7 — Prediction + bandit (Pillar 4)
- [ ] W18 Risk features, LightGBM, ONNX export, onnxruntime scoring, calibration report
- [ ] W19 Thompson sampling + constraints + propensities + rewards + IPS evaluation

## Phase 8 — Refund Watchdog
- [ ] refund-rules.yaml verified against latest RBI circular (date: ____)
- [ ] RefundCaseWorkflow + time-skipping tests
- [ ] Refund Advocate drafts + screens

## Phase 9 — Production cloud and scale
- [ ] W21 Terraform, k3s, Strimzi, Postgres, Redis, Temporal, Ollama, CD, dashboards, CI eval gates
- [ ] W22 Locust load test report; chaos 0 lost / 0 duplicated

## Phase 10 — Proof and presentation
- [ ] §32 metrics table filled with real numbers
- [ ] Competitor comparison table in README
- [ ] Design doc + ADRs (§35)
- [ ] Demo video
- [ ] (Optional) Blog post + Hugging Face model card

## Notes / deferred items
- Phase 0: event models cover the Phase 1 pipeline only (envelope, raw, parsed, clean, cash).
  AgentTask/AgentResult/UserFeedback/NudgeOutcome payloads are added in their phases, because
  §7.3 leaves parts of them loose (`memoryWrites: [..]`, `drafts: [..]`).
- Phase 0: SQLAlchemy ORM models are added per table when a service first uses them (W2);
  Alembic `0001_core` is the schema source.
- Phase 0: kafka-ui, Temporal, Prometheus and Grafana images use `latest`; pin tags in Phase 9.
- Phase 0: LLM smoke test skips until `ollama pull qwen2.5:1.5b` is done.
- W2: a temporary `processor.txn-writer` turns each parsed transaction into one row
  (category OTHER) and publishes `clean-transactions`; W4 dedup/merchants/categories replace it.
- W2: MANUAL_TEXT/voice/widget/bill-photo raw events are skipped by the parser until W3/§21.
- W2: auth is a static per-user API key (`uv run kharcha-admin create-user`); Firebase in W9.
- W2: integration tests start Kafka from `apache/kafka:4.0.0` (same as compose) instead of
  testcontainers' cp-kafka module.
- YOU: `ollama pull qwen2.5:1.5b`, then check real parsing quality with a few of your own messages.
- YOU: create a Telegram bot with @BotFather and put the token in backend/.env as KHARCHA_TELEGRAM_BOT_TOKEN.
- W3 decisions: added BUDGET/FREQUENCY/BROKE_DATE_MOVED triggers + `dedupeKey` to AgentTaskPayload;
  temporary coach consumer in notifier until W8; notifier listens to cash-events for confirmations;
  thin httpx Telegram client (ADR-010 to write).
- W6: per-user merchant/category overrides need a table, so `0003_corrections` was added and the
  §8.1 analyst views become `0004_analyst`. Corrections never change global aliases.
- W8: Coach v0 (notifier) replaced by Coach v1 in `kharcha-agents`; the notifier only gates
  `agent-results`. MCP transport for the tools arrives in W13 (same handlers).
- W4: Alembic `0002_ai` added now (all §8 AI tables) because `kharcha-eval` writes `eval_runs`.
- W4: `kharcha-agents` depends on `kharcha-ml` (path dep) to share parsing metrics; ml must keep
  heavy training deps in optional groups. `kharcha_agents.evals` touches the DB directly; exempt it
  in the import-linter contract (W9) -- the "agents use MCP only" rule is for agent code.
- Phase 0: Temporal runs the dev server (SQLite) locally; the Helm chart is used in Phase 9.
