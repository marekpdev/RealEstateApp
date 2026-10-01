# 🚀 Getting Started

Two ways to run the project. Every command below was run against this repository.

| | **A. Docker Compose** | **B. Local development (uv)** |
|:--|:--|:--|
| Best for | trying the whole system with one command | developing, debugging, running the tests |
| You need | Docker | Python 3.12+, [uv](https://docs.astral.sh/uv/), Postgres 16, Redis 7 (Docker can supply the last two) |
| What runs | Postgres, Redis, migrations, API + UI, worker, beat, NGINX | whatever you start yourself |

**On this page**

- [Configuration](#configuration)
- [A. Docker Compose](#a-docker-compose)
- [B. Local development](#b-local-development)
- [Going live](#going-live-real-models-and-data)
- [Useful tasks](#useful-tasks)

---

## Configuration

Both paths start the same way:

```bash
cp .env.example .env
```

The defaults in `.env.example` give a **fully offline, zero-credential, zero-cost run** (`OFFLINE_MODE=true`):

- every agent returns a deterministic fixture instead of calling a model;
- the market-data API, the knowledge-base sync and the UI log translator are stubbed too;
- any code path that still tries to reach a paid API **raises immediately** rather than spending money.

Everything else still runs for real: the queue, the worker, Postgres persistence, the audit trail, authentication, rate limiting and the live event stream. That is what lets you explore the architecture without keys. `.env.example` documents every variable, with the reasoning next to each. `.env` is git-ignored.

## A. Docker Compose

```bash
docker compose up --build
```

| Service | Role | Reachable |
|:--|:--|:--|
| `nginx` | reverse proxy, the way in to the app | **http://localhost:8080** (UI), `/docs` (Swagger), `/nginx-health` |
| `web_app` | FastAPI API + Chainlit UI | behind NGINX only |
| `worker` | Celery worker running the agent graph | |
| `beat` | Celery Beat (scheduled knowledge-base sync) | |
| `migrate` | one-shot `alembic upgrade head`, exits when done | |
| `postgres` | PostgreSQL 16 | `localhost:5432` |
| `redis` | Redis 7 (queue, pub/sub, cache, rate limits) | `localhost:6379` |

Open http://localhost:8080 and try: *"I would like to invest in Austin, TX with a max budget of $900,000."* You will see each agent appear as a live step.

```bash
docker compose logs -f worker     # watch the agents run inside the worker
docker compose down               # stop
docker compose down -v            # stop and wipe the Postgres volume
```

The services run the code baked into the image (the checkout is deliberately not bind-mounted, and there is no auto-reload), so after changing code re-run `docker compose up --build`. For a fast edit-and-reload loop use the local path below.

## B. Local development

```bash
# 1. Postgres + Redis (or use your own; the defaults in .env.example match these)
docker compose up -d postgres redis

# 2. Dependencies (creates .venv from the lock file)
uv sync

# 3. Apply the database migrations (the dev database must be migrated once)
uv run python -m alembic upgrade head

# 4. API + UI
uv run uvicorn server:app --reload --port 8080

# 5. In a second terminal: the worker that actually runs the agents
uv run celery -A worker.celery_app worker --loglevel=info
```

Then open http://localhost:8080. Optional: start Beat with `uv run celery -A worker.celery_app beat --loglevel=info`, or use the terminal client:

```bash
uv run python cli.py     # asks for a request, enqueues it, waits for the worker, prints the report
```

Notes:

- **Use `server:app`, not `chainlit run app.py`.** The UI is an API client of the same process's `/api/v1`, so it needs the FastAPI app that mounts both.
- **The worker must be running** or jobs stay `pending`.
- **Windows:** Celery's default process pool is not supported there; prefer Docker Compose, or start the worker with `--pool=solo`.
- The seeded local demo user is `demo@realestateapp.local` / `demo-password-123` (see [API.md](API.md)).

## Going live: real models and data

Edit `.env`, add the keys you have, and set `OFFLINE_MODE=false`:

| Variable | Enables |
|:--|:--|
| `OPENAI_API_KEY` | every LLM call and the embeddings |
| `RAPIDAPI_KEY` | real listings for the Market Data agent |
| `BRAVE_API_KEY` | web search (MCP) for the Zoning agent |
| `PINECONE_API_KEY`, `PINECONE_INDEX_NAME` | dense zoning-law retrieval |
| `EMBEDDING_MODEL` | the model that embeds the knowledge base and every query (default `text-embedding-ada-002`); it must match the model the Pinecone index was built with |
| `AZURE_STORAGE_CONNECTION_STRING` | the knowledge-base sync (Azure Blob → Pinecone + Postgres) |
| `JWT_SECRET_KEY` | **must be set to a long random value outside local development** |

You can also go live *partially*: keep `OFFLINE_MODE=false` and set individual `MOCK_*_AGENT_OUTPUT` flags to `true` to mock only some agents.

> **MCP tools need `uvx` and outbound internet.** The Wikipedia, OpenStreetMap, Brave Search and Fetch tool servers are launched with `uvx`, which downloads each one from PyPI on first use. The Docker image ships `uv`/`uvx`, and the local path (B) needs `uv` on the `PATH`. If a server cannot start (no `uv`, or no network), the gateway logs the failed handshake and that run continues without those tools.

Turn on hybrid retrieval (Pinecone + Postgres full-text, fused with RRF) with `HYBRID_RETRIEVAL_ENABLED=true`.

## Useful tasks

```bash
# Score dense vs lexical vs hybrid retrieval (safe: never touches the real index)
uv run python scripts/evaluate_retrieval.py              # dense arm skipped without Pinecone
uv run python scripts/evaluate_retrieval.py --demo-dense # labelled stand-in dense arm, for a full worked example

# Re-sync the RAG knowledge base by hand (canned summary in offline mode)
uv run python -m scripts.sync_knowledge_base

# Measure short polling against SSE watching the same job (needs the API and a worker running)
PYTHONPATH=. uv run python -m scripts.compare_polling_sse
```

**Watch the retry and dead-letter machinery work.** Start the worker with a deliberate fault injected, then submit any job:

```bash
TASK_FAILURE_INJECTION_COUNT=5 uv run celery -A worker.celery_app worker --loglevel=info
```

The first five attempts raise a simulated infrastructure failure. The worker log shows the jittered exponential backoff between attempts, then the last retry is exhausted:

```text
retry: Retry in 1s: SimulatedTaskFailure('Injected failure on attempt 1 of 5')
retry: Retry in 0s: SimulatedTaskFailure('Injected failure on attempt 2 of 5')
retry: Retry in 3s: SimulatedTaskFailure('Injected failure on attempt 3 of 5')
exhausted retries for request 69ec13a2-…
```

The job ends `failed` with `attempt_count = 4` (the original attempt plus three retries), and the task, with its original payload, is parked on the `dead_letter` queue: `redis-cli -n 0 LLEN dead_letter` returns `1`. The delays are random within an exponentially growing ceiling, which is what "full jitter" means. Set the variable back to `0` (the default) for normal operation.

For running the test suite see [TESTING.md](TESTING.md); for deploying to Azure see [DEPLOYMENT.md](DEPLOYMENT.md).
