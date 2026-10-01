# ⚖️ Engineering Decisions

The significant design choices in this project, in a consistent shape: **what was chosen, why, what was rejected, and what it costs.** Every entry describes something that is actually in the code; each links to where. For the system as a whole see [ARCHITECTURE.md](ARCHITECTURE.md), and for the agent-specific design see [AGENTIC_AI.md](AGENTIC_AI.md).

| Area | Decisions |
|:--|:--|
| [**Data and persistence**](#data-and-persistence) | Async SQLAlchemy + Alembic · client-generated UUIDs · normalise what you query · frozen migrations · lazy engine singleton |
| [**Concurrency and correctness**](#concurrency-and-correctness) | The database arbitrates · atomic run claim · payload-fingerprinted idempotency · short transactions |
| [**Asynchronous execution**](#asynchronous-execution) | Celery + Redis · thin task wrapper · dead-letter queue · locks for scheduled jobs · graceful shutdown |
| [**API and security**](#api-and-security) | Versioned contracts · JWT design · anti-enumeration · CORS · token-bucket limiter · SSE · fail-loud vs fail-soft |
| [**Resilience and caching**](#resilience-and-caching) | Selective jittered retries · hand-built circuit breaker · cache-aside with single-flight |
| [**AI systems**](#ai-systems) | Guard at the transport seam · deterministic where possible · Reciprocal Rank Fusion · Postgres full-text · safe evaluation · soft-failing tools |
| [**Delivery**](#delivery) | Multi-stage non-root image · NGINX per-path config · tests against real infrastructure |

---

## Data and persistence

### 1. Async SQLAlchemy 2.0 + asyncpg, with Alembic from day one
- **Chosen:** async ORM and driver; every schema change is an Alembic revision.
- **Why:** async matches FastAPI and the worker's event loop; migrations are the only way a live schema evolves reliably, and testing against a *migrated* database is what makes the drift test meaningful.
- **Rejected:** SQLModel (hides the mechanics), raw SQL (plumbing without structure), `create_all()` (cannot upgrade an existing database).
- **Cost:** more ceremony than a scaffold; async sessions and asyncpg connections are loop-bound, which shapes several later decisions (below).

### 2. Client-generated UUID primary keys
- **Chosen:** `id` defaults to `uuid.uuid4()` in Python ([`db/models.py`](../db/models.py)).
- **Why:** the id exists *before* the `INSERT` completes, so the API can return a job id immediately and hand it to Celery as a plain argument with no extra round trip. Ids are also unguessable.
- **Rejected:** database sequences (the id would only be known after the write returns).
- **Cost:** random UUIDs index less compactly than sequential integers; irrelevant at this scale.

### 3. Normalise what you query, JSONB what you only store
- **Chosen:** market aggregates are real `Numeric(14,2)` columns; the raw listings and each node's output are JSONB. Native Postgres enum for a *closed* domain (job status), `VARCHAR` for an *open* one (node names).
- **Why:** money is never a float; anything filtered or sorted needs a real, indexable column; node names change whenever the graph's topology does and would otherwise need an `ALTER TYPE` per change.
- **Rejected:** a normalised `properties` table (no query pattern needs it), a native enum for node names.
- **Cost:** individual listings cannot be filtered in SQL; acceptable because no feature does that.

### 4. Migrations are frozen artifacts
- **Chosen:** the seeded demo user is its own *data* migration that duplicates its literal values; `sqlalchemy.url` is set in `env.py`, not `alembic.ini`; the native-enum lifecycle is written by hand.
- **Why:** a migration must mean the same thing forever, so it must not import application constants that may change. A DSN routed through `alembic.ini` passes through `ConfigParser` interpolation, where a `%` in a password corrupts it. Autogenerate's default enum output cannot be downgraded and re-upgraded.
- **Cost:** a few duplicated literals and hand edits to generated files, each commented.

### 5. The engine is a lazy module-level singleton, not `app.state`
- **Chosen:** `get_engine()` / `session_scope()` in [`db/session.py`](../db/session.py).
- **Why:** Chainlit's handlers run in a separate FastAPI app mounted with `mount_chainlit()`, which never sees the top-level app's state, so anything on `app.state` is unreachable from them. Building the engine lazily also avoids freezing configuration at import time.
- **Cost:** process-wide state; each Celery task disposes it inside its own event loop (see decision 12).

## Concurrency and correctness

### 6. The database arbitrates uniqueness: insert, then catch the conflict
- **Chosen:** `create_idempotent()` attempts the `INSERT` and catches `IntegrityError`, then re-selects the winner ([`db/repositories/investment_request_repository.py`](../db/repositories/investment_request_repository.py)).
- **Why:** `SELECT` then `INSERT` is a check-then-act race; two concurrent callers can both see "no row" and both insert. Only the constraint, enforced atomically by the database, can decide. A real two-connection race test proves one insert and one replay.
- **Rejected:** `SELECT … FOR UPDATE` (there is no row to lock before it exists), an application-level Redis mutex (a second source of truth).
- **Cost:** after an `IntegrityError` the session must be rolled back, so this method must be the only thing its transaction does.

### 7. An atomic conditional `UPDATE` claims a run
- **Chosen:** `try_claim_run()` is `UPDATE … SET status='running' WHERE id=? AND status IN ('pending','failed') RETURNING id`.
- **Why:** a broker that delivers at-least-once can hand the same task to two workers *at once*; a read-then-branch check covers only redelivery after the first run finished. The row-level lock on the `UPDATE` lets exactly one caller win.
- **Rejected:** read-then-branch; catching a duplicate-report `IntegrityError` (it fires only *after* the paid agents ran); a Redis `SETNX` lock (a competing source of truth for job ownership).
- **Cost:** a worker that is hard-killed mid-run leaves the job `running` and nothing reclaims it. A lease with a heartbeat plus a reaper is the known next step.

### 8. Idempotency keys with a payload fingerprint
- **Chosen:** `Idempotency-Key` is required on `POST /reports`. The request body's SHA-256 is stored at claim time; same key + same body replays (`200`), same key + different body is `409`. The comparison lives in `claim_request()`, not in the repository. A replay of an unfinished job still re-enqueues.
- **Why:** a `POST` is not idempotent by definition; the key restores safe retries. The claim function returns the mismatch as data (`ClaimResult.payload_conflict`) rather than raising, because the CLI calls it directly and replays regardless of the retried text; only the HTTP layer turns a mismatch into `409`. The original enqueue may never have reached the broker, so a replay must re-enqueue; the atomic claim makes that safe.
- **Rejected:** widening `UNIQUE` to include the hash (two rows could then exist for one key, the opposite of what a key is for); an optional header (a client that omits it gets no safety, silently); de-duplicating on body alone (conflates "same text" with "same request").
- **Cost:** a required header is a small burden on clients; a fixed-size hash rather than stored text means the original body is not recoverable from the key row.

### 9. No transaction spans the agent run
- **Chosen:** every write in the run recorder is its own short `session_scope()`.
- **Why:** the graph makes multi-minute LLM calls; a transaction held that long would pin a pooled connection idle and block autovacuum on every table it touched.
- **Cost:** a run is not atomic across nodes, which is why the audit rows and the final report/status commit are designed to be independently correct.

## Asynchronous execution

### 10. Celery + Redis, with `acks_late` and a prefetch of one
- **Chosen:** Redis as broker and result backend on separate logical databases; `task_acks_late=True`; `worker_prefetch_multiplier=1`; JSON serialisation ([`worker/celery_app.py`](../worker/celery_app.py)).
- **Why:** tasks are few, long and expensive. Late acknowledgement means a crashed worker's task is redelivered rather than lost; prefetch one stops a worker hoarding tasks while siblings idle; JSON avoids pickle's code-execution-on-deserialise risk.
- **Rejected:** RQ (fewer primitives for retry, routing and dead-lettering), Dramatiq (smaller ecosystem), SQS (couples the app to AWS while it runs on Azure).
- **Cost:** delivery is now at-least-once, which is precisely why decisions 6-8 exist.

### 11. The task is a thin wrapper; LangGraph orchestrates *within* a run, Celery *when and where*
- **Chosen:** `generate_report` only calls `run_claimed_request()`. Neither framework knows the other exists.
- **Why:** each can be tested and replaced independently, and the graph can still be run inline (the CLI's persistence-off path, the tests).
- **Cost:** a small amount of glue.

### 12. One event loop per task means disposing loop-bound resources
- **Chosen:** each task runs `asyncio.run(...)` and disposes the database engine and the async Redis clients in a `finally`.
- **Why:** `asyncio.run()` opens and closes a new loop per call, but asyncpg and Redis connections are bound to the loop that made them. A cached engine from the first task would fail every later task with "attached to a different loop". This surfaced in practice as a real worker process crashing on its *second* task, which is why the disposal is explicit.
- **Cost:** connections are rebuilt per task, negligible next to a multi-minute run.

### 13. A dead-letter queue nothing consumes
- **Chosen:** after the last retry the job is marked `failed` and the task (with its original payload) is routed to a dedicated `dead_letter` queue ([`worker/tasks.py`](../worker/tasks.py)).
- **Why:** Redis has no first-class DLQ; a parked message preserves what is needed to inspect or replay it, whereas retrying a poison message forever helps nobody.
- **Rejected:** a Postgres table (it would survive a Redis flush and be queryable, and is the natural upgrade).
- **Cost:** a Redis list is neither durable across a flush nor queryable.

### 14. A Redis lock for the scheduled sync, not "just run one instance"
- **Chosen:** the knowledge-base sync takes `SET NX EX` and releases via a token-checked compare-and-delete Lua script.
- **Why:** Beat fires by wall clock, not by "did the last run finish", so a slow sync can overlap its successor even with a single Beat and a single worker. A plain `DEL` could release a lock a later run legitimately acquired after this run's own TTL expired.
- **Rejected:** a host `cron` or Kubernetes `CronJob` (a second scheduling system), a Postgres claim row (the sync has no per-invocation identity to dedupe on), a longer interval (an assumption about runtime, not a guarantee).
- **Cost:** the lock TTL must comfortably exceed a real sync's duration.

### 15. Verify framework shutdown behaviour instead of writing signal handlers
- **Chosen:** no custom `SIGTERM` code. Grace periods (330 s) are set where the signal originates.
- **Why:** reading the source and running real processes showed both Celery's warm shutdown and uvicorn's graceful shutdown already wait for in-flight work on a single `SIGTERM` and refuse new work. The actual gap was the orchestrators' default grace periods (10 s / 30 s), which would `SIGKILL` a multi-minute run. 330 s is derived from the app's own 300 s report deadline plus cleanup headroom.
- **Cost:** 330 s is a considered estimate, not a measured ceiling: there is no per-task time limit to derive it from.

## API and security

### 16. Versioned, contract-first API with separate DTOs
- **Chosen:** URL-path versioning (`/api/v1`), explicit Pydantic request/response models in [`api/v1/schemas.py`](../api/v1/schemas.py), correct status codes (`202` accepted, `200` replay, `409`, `401`, `404`, `422`, `429`, `503`).
- **Why:** a path version is visible in every log line and `curl` command with no client plumbing; the graph's internal state must be free to change without breaking the public contract.
- **Rejected:** header or media-type versioning; exposing graph state directly.
- **Cost:** a second set of models to maintain.

### 17. JWT access + refresh, verified statelessly, hashed with bcrypt
- **Chosen:** HS256; 15-minute access and 7-day refresh tokens with a `type` claim; plain JSON login (no OAuth2 form flow); `bcrypt` called directly ([`auth/`](../auth/)).
- **Why:** one process both issues and verifies, so there is no second verifier that needs a public key. Short access tokens bound a leaked token's blast radius. This system issues JWTs; it does not implement an OAuth2 grant. passlib's bcrypt backend probes a version attribute that recent bcrypt releases removed, which breaks it, so `bcrypt` is used directly.
- **Rejected:** RS256 (buys nothing with a single issuer/verifier), refresh-token rotation *without* a server-side record (it would invalidate nothing while implying it does).
- **Cost:** a refresh token cannot be revoked before it expires; a `jti` denylist is the real fix.

### 18. Anti-enumeration and uniform authentication failures
- **Chosen:** another user's job is a `404`, not a `403`; every authentication failure (missing, expired, malformed, wrong type, unknown user) is the same `401`.
- **Why:** a `403` would confirm that the id exists; distinct failure messages tell an attacker *why* a token failed.

### 19. CORS with explicit origins; a wildcard is a startup error
- **Chosen:** `CORS_ALLOWED_ORIGINS` defaults to empty (trust nobody); a literal `*` raises at import time ([`config/config.py`](../config/config.py)); `expose_headers` lets browser clients read `Retry-After` and `X-RateLimit-*`.
- **Why:** CORS is enforced by browsers, not servers; it protects a *different site's* JavaScript from reading a logged-in user's responses. Credentialed requests cannot use a wildcard, so a misconfiguration should fail at boot, not on the first cross-origin request.

### 20. A Redis token bucket as one atomic Lua script, as a dependency
- **Chosen:** [`rate_limit/token_bucket.py`](../rate_limit/token_bucket.py): the check and the spend are one Lua script, time comes from Redis' own `TIME`, and the limiter is a FastAPI `Depends()` keyed by the authenticated identity.
- **Why:** check-then-decrement in application code lets two concurrent requests both read "one token left" and both spend it. Reading time from Redis, not Python, keeps every replica in agreement about the bucket even with clock skew. The identity only exists once authentication has run inside FastAPI's dependency system; ASGI middleware executes ahead of that and cannot see it.
- **Rejected:** a fixed window (permits a double burst across a window boundary), a sliding-window log (needless precision and memory), IP-keyed middleware (the wrong identity behind a proxy).
- **Cost:** one gotcha worth recording: Lua numbers become integers in Redis replies, silently truncating a fractional token count, so the script returns them as strings.

### 21. Server-Sent Events, hand-rolled, snapshot then live
- **Chosen:** a plain `StreamingResponse` over Redis pub/sub with a Postgres-backed `snapshot`, a separate `status` event, and heartbeats (details in [ARCHITECTURE.md](ARCHITECTURE.md#live-progress-with-server-sent-events)).
- **Why:** data flows one way, so a WebSocket's second channel buys nothing; polling is wasteful and laggy by construction. Pub/sub is ephemeral, so late subscribers need a durable snapshot. The job's end is read from the database rather than inferred from a node name, to stay decoupled from the graph's topology.
- **Rejected:** WebSockets, plain polling as the only option, a library (the wire format is a tiny formatter, and hand-writing it keeps the mechanism visible).
- **Cost:** each open stream holds a Redis subscription and a connection, so connection count is the scaling constraint to watch.

### 22. Opposite failure priorities are not an inconsistency
- **Chosen:** the rate limiter fails **loudly** on a Redis outage; progress events fail **softly**.
- **Why:** a limiter that fails open is defeated exactly when infrastructure is stressed, so it must not swallow errors. A progress event is an ephemeral notification on top of state already committed to Postgres; letting it abort a multi-minute, already-paid run would be strictly worse than losing one message.

## Resilience and caching

### 23. Retry only what is transient, with full jitter and a time budget
- **Chosen:** tenacity in [`services/base_api_client.py`](../services/base_api_client.py): timeouts, connection errors, `429` and `5xx` retry; other `4xx` fail immediately; `wait_random_exponential`; attempts **and** elapsed time both bound the loop.
- **Why:** resending a bad request just fails again; fixed backoff makes callers retry in lockstep; an attempt cap alone does not bound latency once backoff grows.
- **Cost:** retries compose with Celery's own (amplification), which is why the circuit breaker sits in front of them.

### 24. A hand-built, per-upstream circuit breaker
- **Chosen:** a small state machine ([`resilience/circuit_breaker.py`](../resilience/circuit_breaker.py)): closed → open after N consecutive failures → half-open after a timeout, with exactly one probe. In-memory and process-local.
- **Why:** the three states and their transitions *are* the concept, so a dependency adds little; one breaker per upstream so a sick vendor cannot block a healthy one; only "the vendor looks unhealthy" counts (a caller's own `4xx` never does); a failed probe re-opens immediately.
- **Rejected:** a library, a single global breaker, a failure-rate-over-a-window trigger (more code for no benefit here).
- **Cost:** breakers are not shared across replicas, so each learns of an outage independently.

### 25. Cache-aside, TTL-only invalidation, single-flight, `volatile-lru`
- **Chosen:** [`cache/market_data_cache.py`](../cache/market_data_cache.py): a hashed, normalised key; a TTL as the *entire* invalidation strategy; a `SET NX EX` single-flight lock; Redis eviction set to `volatile-lru`.
- **Why:** the caller is the only thing that knows both where the cache is and how to ask the vendor, so cache-aside is the natural fit; nothing writes fresh market data independently of a read, so there is no event to invalidate on, only staleness to bound. Without the lock, N concurrent misses each pay for the vendor. Eviction policy is server-wide, so `allkeys-lru` could evict a pending broker message, while `volatile-lru` only touches keys that have an expiry.
- **Rejected:** read-through (needs a caching layer this app does not have), write-through (the app never writes market data), `allkeys-lru`.
- **Cost:** results can be up to 15 minutes stale by design.

## AI systems

### 26. The offline guard lives at the HTTP transport seam
- **Chosen:** [`config/safety.py`](../config/safety.py) wraps the transport that every LLM, embedding and vendor client is built on, and raises `OfflineModeViolation` per request.
- **Why:** a spend leak is a *code* mistake (a path that forgot a mock flag); a startup check only catches configuration mistakes, and monkeypatching one method misses the several entry points agent wrappers create. Every real call must cross the transport.
- **Cost:** the exception must subclass the SDK's own error type, or the SDK swallows and retries it.

### 27. Deterministic where possible
- **Chosen:** the supervisor has no LLM call, market statistics are computed in Python, and the modeler delegates arithmetic to a tool.
- **Why:** a model adds latency, cost and a failure mode; it should be spent where judgement is needed.

### 28. Reciprocal Rank Fusion over weighted score fusion
- **Chosen:** [`retrieval/hybrid_search.py`](../retrieval/hybrid_search.py): dense and lexical lists fused by `Σ 1/(k+rank)`, joined by exact chunk content, run in parallel, degrading to whichever side is available.
- **Why:** the two scores are on unrelated scales; ranks need no normalisation. Both indexes are built from identical chunks, so joining by content needs no id mapping.
- **Rejected:** weighted score fusion (fragile normalisation and tuning), a cross-encoder reranker (another model dependency per candidate; a sensible later step).

### 29. Postgres full-text search for the lexical half
- **Chosen:** a generated `tsvector` column with a GIN index, a full-table replace on resync, `websearch_to_tsquery`.
- **Why:** the data is already in Postgres, so this adds no new service. A generated column keeps the index correct by construction (no trigger to forget). Full replace expresses "this is everything currently in the source" without an external id. `websearch_to_tsquery` accepts ordinary search syntax and never raises on malformed input.
- **Rejected:** Elasticsearch or a BM25 extension (a new service for one table), a trigger-maintained column.
- **Cost:** Postgres full-text ranking is not true BM25, and a query ANDs its words together.

### 30. Evaluate retrieval without endangering the live index
- **Chosen:** [`scripts/evaluate_retrieval.py`](../scripts/evaluate_retrieval.py) runs inside one transaction that is never committed; metrics are binary-relevance recall@k, MRR and nDCG@k; results are broken down by query type.
- **Why:** re-seeding a synthetic corpus into `document_chunks` would otherwise destroy the table the live agent searches. Binary relevance is adequate for a small hand-labelled set. The per-category breakdown is what surfaced that lexical search scores zero on paraphrases.

### 31. Tools fail soft, in language the agent understands
- **Chosen:** a degraded answer ("…temporarily unavailable, use the web search tool instead") rather than an exception.
- **Why:** the agent's own prompt already treats "no results" as the cue to fall back, so the message plugs into an existing recovery path and the graph completes.

## Delivery

### 32. A multi-stage image that runs as a non-root numeric user
- **Chosen:** a builder stage installs locked production dependencies into a virtualenv; the runtime stage copies only that venv and the code, from the *same* slim base, with no test tooling, only the standalone `uv`/`uvx` binaries the agents need to launch MCP tool servers, a fixed UID/GID (10001), and dependency layers ordered before code ([`Dockerfile`](../Dockerfile)).
- **Why:** the venv holds a symlink to its interpreter, so both stages must share a base; `uv run` at start-up may re-resolve and want a writable cache, which a non-root process lacks; a numeric UID is what `runAsNonRoot` can verify.
- **Rejected:** Alpine (musl libc means the prebuilt manylinux wheels several dependencies ship, such as asyncpg, bcrypt and pydantic-core, do not apply, forcing source builds), deleting files in a later layer (never shrinks an image).
- **Cost:** the test fixtures ship in the image on purpose, because offline mode loads canned outputs from them at runtime.

### 33. NGINX configured per path, resolving its upstream per request
- **Chosen:** [`nginx/default.conf`](../nginx/default.conf): an unbuffered SSE location, an explicit WebSocket upgrade map, and an upstream addressed through a variable plus Docker's resolver.
- **Why:** blanket `proxy_buffering off` would disable a useful default for every ordinary response and hide which route is special. A plain `upstream` block pins the container's IP at startup, so recreating `web_app` turns every request into a `502` until NGINX restarts, even though the app is healthy.
- **Cost:** a variable `proxy_pass` cannot use an `upstream` block, so there is no keep-alive pool to the app; negligible on one Docker network. There is no TLS or edge rate limiting yet.

### 34. Tests run against real infrastructure, isolated by rollback
- **Chosen:** a real Postgres and Redis; a dedicated test database created and migrated with Alembic once per session; each test in one connection, one transaction, savepoints underneath, rolled back at teardown; a drift test comparing models to the migrated schema; real concurrency races.
- **Why:** a mocked database cannot verify a unique constraint, a cascade or a race, which are the entire point of several features. Rollback is strictly stronger than truncation and faster than recreating the schema.
- **Cost:** running the suite needs Postgres and Redis (see [TESTING.md](TESTING.md)).
