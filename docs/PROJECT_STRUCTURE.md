# 📂 Project Structure

Where things live, and which document explains each part. Directory names follow the architecture: the request path (`api/`, `auth/`, `rate_limit/`), the work path (`worker/`, `orchestration/`, `agents/`), and the shared plumbing both rely on.

**On this page**

- [Map](#map)
- [How the pieces depend on each other](#how-the-pieces-depend-on-each-other)
- [Where to start reading](#where-to-start-reading)
- [Regenerating the diagrams](#regenerating-the-diagrams)

---

## Map

```text
├── agents/               six LangGraph nodes, each with a mock twin for offline runs
├── graph.py              the graph topology: nodes, fan-out, fan-in
├── schema/               typed graph state and the market-data models
├── tools/                MCP gateway (tool discovery), RAG and hybrid-retrieval tools
├── retrieval/            hybrid search, Reciprocal Rank Fusion, evaluation metrics and dataset
├── api/v1/               versioned REST API: auth, reports, the SSE stream
├── auth/                 JWT, bcrypt password hashing, service API keys
├── rate_limit/           Redis token bucket (one atomic Lua script)
├── orchestration/        run recorder: idempotent claim, run, audit trail, publish
├── worker/               Celery app, the generate_report task, retries, dead-letter queue, Beat schedule
├── events/               versioned progress-event schema and the Redis pub/sub publisher
├── db/                   SQLAlchemy models, repositories, session handling
├── alembic/              database migrations
├── services/             resilient HTTP client, market-data gateway, vector store, API client for the UI
├── resilience/           circuit breaker
├── cache/                Redis cache-aside with a single-flight lock
├── config/               environment-driven settings and the offline-mode guard
├── logger/               live step rendering and the log-to-plain-English translator
├── app.py                Chainlit chat UI (an ordinary client of the public API)
├── server.py             FastAPI application: mounts the API and the UI, health probes
├── cli.py                terminal client
├── scripts/              knowledge-base sync, retrieval evaluation, polling-versus-SSE comparison
├── tests/                automated tests against real PostgreSQL and Redis
├── nginx/                reverse-proxy configuration
├── Dockerfile · docker-compose.yml     multi-stage image and the local stack
├── k8s/ · terraform/     Kubernetes manifests and Azure infrastructure as code
├── .github/workflows/    CI: test against real services, then build and push the image
└── docs/                 this documentation, and docs/images/ (the diagrams and their generator)
```

## How the pieces depend on each other

The dependency arrow always points one way, which is what keeps each part testable on its own:

- `agents/` and `graph.py` never import the database. The run recorder in `orchestration/` wraps the compiled graph from the outside and records what it observes.
- `api/v1/` never runs an agent. It records a job in PostgreSQL and enqueues a task in Redis; `worker/` picks it up.
- The Chainlit UI in `app.py` never imports the graph. It logs in and streams through `/api/v1` like any other client.
- `config/safety.py` sits underneath every outbound HTTP client, which is why one switch (`OFFLINE_MODE`) can stop all paid calls.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full picture.

## Where to start reading

| If you want to see | Open |
|:--|:--|
| the agent graph in one small file | [`graph.py`](../graph.py) |
| how a request becomes a job and a stream | [`api/v1/reports.py`](../api/v1/reports.py) |
| how a job runs exactly once | [`orchestration/run_recorder.py`](../orchestration/run_recorder.py) |
| how tools are discovered over MCP | [`tools/tools.py`](../tools/tools.py) |
| how retrieval is fused and measured | [`retrieval/hybrid_search.py`](../retrieval/hybrid_search.py), [`scripts/evaluate_retrieval.py`](../scripts/evaluate_retrieval.py) |
| how a paid call is made resilient | [`services/base_api_client.py`](../services/base_api_client.py) |
| how tests get real infrastructure | [`tests/conftest.py`](../tests/conftest.py) |

## Regenerating the diagrams

The architecture diagrams are SVG files generated from one description each, in a light and a dark variant, so the two can never drift apart. The docs embed them with a `<picture>` element, and GitHub shows the variant that matches the reader's theme. Sequence diagrams and the data-model diagram stay in Mermaid, where the notation is the point.

```bash
python docs/images/generate.py                       # every diagram
python docs/images/generate.py agent-graph           # just one
```

The generator uses only the Python standard library ([`docs/images/generate.py`](images/generate.py) and [`svgkit.py`](images/svgkit.py)). The nDCG chart's numbers are pinned by a test, so they cannot go stale unnoticed.
