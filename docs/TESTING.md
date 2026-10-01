# 🧪 Testing

**245 tests, about 25 seconds**, run against a **real PostgreSQL and a real Redis**. The suite makes no paid API calls and needs no credentials.

**On this page**

- [Running the tests](#running-the-tests)
- [What is tested](#what-is-tested)
- [How the harness works](#how-the-harness-works)
- [Testing agents without paying for them](#testing-agents-without-paying-for-them)
- [What is deliberately not covered](#what-is-deliberately-not-covered)
- [CI](#ci)

---

## Running the tests

```bash
cp .env.example .env                           # once: Compose reads it, even to start just two services
docker compose up -d postgres redis            # real infrastructure (or use your own)
uv sync
uv run python -m alembic upgrade head          # migrate the dev database once
uv run pytest
```

- **Migrate first.** The harness creates and migrates its own `<db>_test` database for the tests that need transaction-per-test isolation, but the worker and Celery tests write to the plain dev database directly, which has no tables until it is migrated. On an un-migrated database those tests fail; after `alembic upgrade head` all 245 pass.
- **Postgres role.** The role needs `CREATEDB` (the harness creates the test database). The Compose Postgres user already has it; on a hand-made local role add it: `CREATE ROLE realestateapp LOGIN PASSWORD 'realestateapp' CREATEDB;`.
- **With or without a `.env`.** The suite passes in all three set-ups: no `.env`, a `.env` copied from `.env.example` (`OFFLINE_MODE=true`), and `OFFLINE_MODE=true` exported. A test that depends on the ambient value of a flag patches it explicitly.
- Run one area: `uv run pytest tests/test_repositories.py`, or one test with `-k`.

## What is tested

| Area | Files | What is proven |
|:--|:--|:--|
| **Agents and graph** | `test_agents.py`, `test_offline_graph_run.py`, `test_tools.py`, `test_utils.py` | each node's contract; the compiled graph runs end to end with zero network calls; MCP gateway filtering, schema sanitising and token-safe defaults |
| **Persistence** | `test_repositories.py`, `test_db_migrations.py`, `test_db_session_isolation.py`, `test_run_recorder.py` | unique constraints, cascades, upserts, the idempotent claim and the atomic run claim (including **real concurrent races**), the migration drift check, one audit row per node, parallel nodes sharing a start time |
| **Worker** | `test_worker.py` | a real Celery worker round-trips tasks through real Redis; `acks_late` and prefetch settings; jittered retry configuration; recovery within the retry limit; attempt counting; the dead-letter handoff; redelivery short-circuiting; the Beat schedule and its overlap lock (held, released, released on error) |
| **HTTP API** | `test_api_reports.py`, `test_api_auth.py`, `test_api_keys.py`, `test_cors.py`, `test_rate_limiting.py`, `test_openapi_snapshot.py` | every status code (`200`, `202`, `401`, `404`, `409`, `422`, `429`, `503`), idempotency replay and conflict, JWT expiry and token-type checks, ownership scoping, API keys, CORS allow and deny, rate-limit headers and refill, and the committed OpenAPI snapshot matching the routes actually served at `/openapi.json` |
| **Live progress** | `test_api_reports_stream.py`, `test_events.py` | snapshot then live, heartbeats, client disconnect releasing the subscription, the bounded terminal-status poll, `log` events not ending a run, the versioned event schema, publishing failures being swallowed |
| **UI as an API client** | `test_app_api_client.py`, `test_app_wiring.py`, `test_logger_steps.py` | login and refresh-once-on-`401`, the SSE line parser, live step rendering, the UI submitting through the real API |
| **Resilience** | `test_api_clients.py`, `test_circuit_breaker.py`, `test_market_data_cache.py` | retry only transient failures, never `4xx`, bounded budgets; every breaker transition including the half-open edge cases (with a controllable clock); cache hit/miss, TTL expiry, and **5 concurrent callers producing exactly 1 vendor call** |
| **Retrieval** | `test_hybrid_search.py`, `test_hybrid_retrieval_tools.py`, `test_retrieval_evaluation.py`, `test_vector_tools.py`, `test_vector_store.py`, `test_sync_knowledge_base.py` | the RRF arithmetic, graceful degradation when either half fails, metric functions against hand-computed values, the evaluation results shown in the docs (pinned, so the chart and table cannot go stale), the pinned embedding model, the sync's chunk parity and independent failure domains |
| **Offline safety** | `test_offline_mode.py` | `OFFLINE_MODE` implies every mock flag; the guard raises on any paid call; the transport behaves normally when online |

## How the harness works

The interesting part is [`tests/conftest.py`](../tests/conftest.py):

- **A dedicated test database, migrated by Alembic.** Once per session it drops and recreates `<db>_test` and runs the real migrations, never `create_all()`. That is what makes the **drift test** meaningful: it compares the models against a schema produced *by migrating*, so a model change without a migration fails the build.
- **Isolation by rollback, not truncation.** Each test gets one connection and one outer transaction, with the session bound in `join_transaction_mode="create_savepoint"`, so application code that itself commits is really committing a savepoint. Everything is rolled back at teardown. This is faster than recreating the schema and strictly stronger than truncating (truncation must run after the test, even a failed one).
- **Application code needs no test-only parameters.** The `db_session` fixture temporarily rebinds `db.session`'s module-level engine and session factory to the test connection, so code that calls `session_scope()` internally transparently joins the test transaction.
- **Real concurrency, on separate connections.** The idempotent-claim and run-claim race tests run `asyncio.gather` over *separate* connections with their own session factory and explicit cleanup, because two coroutines cannot share one connection's savepoint stack. They assert exactly one winner, which a mocked database could never do.
- **Shared real Redis, kept clean.** Autouse fixtures flush the rate-limit keys and market-data cache, reset the in-memory circuit breakers, and dispose the async Redis clients after each test so no state or event loop leaks between tests.
- **Real Celery, not eager mode.** Worker tests use Celery's embedded test worker, because `task_always_eager` would skip the broker and prove nothing about delivery.

## Testing agents without paying for them

- **A mock twin per agent** returns a fixture (`tests/fixtures/*.json`) validated against the *same* Pydantic model as the real output, so downstream code is exercised on the true shape.
- **`OFFLINE_MODE`** turns every mock on and makes the HTTP transport under every LLM, embedding and vendor client raise on any attempted call.
- **The suite passes with the guard armed** (`OFFLINE_MODE=true`), which is the proof that no test leaks a paid call.
- **Vendor HTTP is mocked with `respx`** at the network boundary, so the real client code (retries, breakers, error mapping) still runs.

Mocks keep the *shape* identical so the plumbing is verified; they say nothing about model quality. That is what the [retrieval evaluation harness](AGENTIC_AI.md#retrieval-evaluation) and real runs are for.

## What is deliberately not covered

- **Live model, vendor and Pinecone behaviour.** These are paid and non-deterministic; the boundary is mocked instead (for Pinecone, at the SDK boundary).
- **Retry exhaustion into the dead-letter queue through the embedded test worker.** That worker's teardown does not reliably tolerate a task whose failure callback is still running at shutdown, so the path is instead covered in pieces (the retry configuration, attempt counting, the failure handler and the dead-letter handoff each have their own tests) and verified end to end against a real standalone worker; see the fault-injection walkthrough in [GETTING_STARTED.md](GETTING_STARTED.md#useful-tasks).
- **Mid-run SSE streaming across separate processes.** The automated tests prove the snapshot path and drive the live path against a fake stream; genuinely concurrent worker-plus-API streaming was verified against real standalone processes (see the captured transcript in [API.md](API.md#live-progress-over-sse)).
- **Load and performance testing.**

## CI

[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) runs the same suite on every push and pull request to `main`, with Postgres 16 and Redis 7 as **service containers**: migrate the database, run `pytest`, and only if that passes build and push the Docker image. Real infrastructure in CI means the constraint, cascade and race tests mean the same thing there as on a laptop.
