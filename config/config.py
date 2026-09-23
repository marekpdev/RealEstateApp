import os
from dotenv import load_dotenv
from utils.utils import get_env_bool

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY")
GH_TOKEN = os.getenv("GH_TOKEN")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "real-estate-app")

# Deliberately POSTGRES_DSN, never DATABASE_URL: chainlit/data/__init__.py activates
# Chainlit's own persistence layer the moment DATABASE_URL is set in the environment,
# then chokes on the postgresql+asyncpg:// scheme this app uses.
POSTGRES_DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql+asyncpg://realestateapp:realestateapp@localhost:5432/realestateapp",
)
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
DB_ECHO = get_env_bool("DB_ECHO")
# Escape hatch for environments with no database at all. Everywhere else, an
# unreachable database must fail loudly rather than silently record nothing.
DB_PERSISTENCE_ENABLED = get_env_bool("DB_PERSISTENCE_ENABLED", default=True)

# Broker (task queue) and result backend (task state/return values) are two
# separate logical stores, kept on separate Redis logical databases so a
# queue-inspection command never scans through result keys and vice versa -
# even though both point at the same Redis server by default.
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

# The graph now runs inside a Celery worker process, not inline in the
# request handler - cli.py enqueues the work directly and polls the
# investment_requests row itself; app.py enqueues and polls too, but only
# ever through its own HTTP calls to GET /api/v1/reports/{id} (see
# services/report_api_client.py), never by touching the row directly. Both
# still share the same poll_interval/timeout values so their behavior is
# observably identical from a user's point of view. poll_interval trades
# responsiveness for load (a database read for cli.py, an HTTP request for
# app.py); timeout is a safety net so a caller never waits forever on a job
# whose worker died without reaching a terminal status.
REPORT_POLL_INTERVAL_SECONDS = float(os.getenv("REPORT_POLL_INTERVAL_SECONDS", "1.0"))
REPORT_POLL_TIMEOUT_SECONDS = float(os.getenv("REPORT_POLL_TIMEOUT_SECONDS", "300"))

# --- Chainlit as an API client ---------------------------------------------
# app.py no longer imports the LangGraph graph or the orchestration/
# persistence layer directly - its Chainlit handlers talk to this same
# process's own /api/v1 HTTP surface over a real loopback connection,
# exactly like any other API client would (see services/report_api_client.py).
# API_BASE_URL is where that surface is actually listening; the default
# matches the port this repo's own Dockerfile/uvicorn command binds (see
# docker-compose.yml's web_app service), which is also where Chainlit itself
# ends up mounted (server.py's mount_chainlit() call) - so the default is
# correct unmodified in both Docker and a local `uvicorn server:app --port
# 8080` run.
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8080")

# Credentials app.py logs in with (POST /api/v1/auth/login) to authenticate
# its own report requests as the seeded demo user, instead of reading
# Postgres directly. Must match the row alembic/versions/
# 45ed0d458cdf_seed_demo_user_login_password.py seeds - that migration
# duplicates this same literal password rather than importing it from here,
# since a migration must stay a frozen historical artifact (see its own
# comment) and predates this constant existing at all. Override both
# together if a real deployment reseeds that row with different credentials.
DEMO_USER_EMAIL = os.getenv("DEMO_USER_EMAIL", "demo@realestateapp.local")
DEMO_USER_PASSWORD = os.getenv("DEMO_USER_PASSWORD", "demo-password-123")

# generate_report's retry policy. TASK_MAX_RETRIES bounds how many times a
# task that raises can be redelivered before it's treated as poison and
# routed to the dead-letter queue instead of retried forever.
# TASK_RETRY_BACKOFF_BASE_SECONDS/TASK_RETRY_BACKOFF_MAX_SECONDS feed
# Celery's retry_backoff (exponential: base * 2**retries, capped at max) -
# kept small here (versus Celery's own 1s/600s defaults) so a real worker
# exhausting retries in a test doesn't need to wait minutes to do it.
TASK_MAX_RETRIES = int(os.getenv("TASK_MAX_RETRIES", "3"))
TASK_RETRY_BACKOFF_BASE_SECONDS = int(os.getenv("TASK_RETRY_BACKOFF_BASE_SECONDS", "1"))
TASK_RETRY_BACKOFF_MAX_SECONDS = int(os.getenv("TASK_RETRY_BACKOFF_MAX_SECONDS", "8"))
# The Redis list generate_report routes a task to once TASK_MAX_RETRIES is
# exhausted. Nothing consumes it automatically - a poison message sitting
# here is exactly the point (see worker/tasks.py's dead_letter task).
DEAD_LETTER_QUEUE_NAME = os.getenv("DEAD_LETTER_QUEUE_NAME", "dead_letter")
# Deliberate failure-injection knob, off (0) by default. Set to N to make
# generate_report's first N physical attempts (the original try plus every
# retry) raise a simulated transient failure before doing any real work -
# the only practical way to exercise the retry/backoff/DLQ path on demand
# without actually breaking Postgres or Redis. See worker/tasks.py.
TASK_FAILURE_INJECTION_COUNT = int(os.getenv("TASK_FAILURE_INJECTION_COUNT", "0"))

# How often (seconds) Celery Beat re-enqueues worker.sync_knowledge_base
# (worker/celery_app.py's beat_schedule). A plain number is interpreted by
# Celery as a timedelta in seconds. Real zoning PDFs in Azure Blob Storage
# change on the order of days, not seconds - the 6-hour default reflects
# that - but it's an env var specifically so a short interval (e.g. 10) can
# be set for local/CI verification without editing code.
KNOWLEDGE_BASE_SYNC_SCHEDULE_SECONDS = float(
    os.getenv("KNOWLEDGE_BASE_SYNC_SCHEDULE_SECONDS", "21600")
)
# TTL (seconds) on the Redis lock worker.sync_knowledge_base holds for the
# duration of one sync, so a run that overlaps its own still-running
# predecessor (a short schedule, or two Beat-fed workers) is a no-op instead
# of a second concurrent ingest. Also the safety net if a worker dies while
# holding the lock: the lock self-expires instead of wedging every future
# run forever. Must comfortably exceed how long a real sync can take.
KNOWLEDGE_BASE_SYNC_LOCK_TTL_SECONDS = int(
    os.getenv("KNOWLEDGE_BASE_SYNC_LOCK_TTL_SECONDS", "1800")
)

# JWT access/refresh tokens. HS256 (symmetric) rather than
# RS256/asymmetric: one process both issues and verifies tokens here, so
# there's no second service that needs to verify without holding the
# signing secret - the scenario asymmetric signing actually buys you.
# The default secret below is fine for the offline/dev/CI posture this repo
# runs under (see OFFLINE_MODE); a real deployment must override it with a
# long random value via the JWT_SECRET_KEY env var.
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-only-insecure-secret-change-before-any-real-deployment")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
# Short-lived on purpose: the blast radius of a leaked access token is
# bounded by how soon it stops working on its own. The refresh token is
# long-lived so a client doesn't have to re-prompt for a password every 15
# minutes, and is only ever sent to POST /api/v1/auth/refresh, not on every
# request.
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
JWT_REFRESH_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_MINUTES", "10080"))  # 7 days

# --- CORS ----------------------------------------------------------------
# Comma-separated exact origins (scheme + host + port) allowed to make
# cross-origin browser requests against this API, e.g.
# "https://app.example.com,http://localhost:3000". Empty by default: unlike
# JWT_SECRET_KEY, there's no safe default value here, so with nothing
# configured every cross-origin browser request is rejected until a
# deployment explicitly opts specific origins in. Never "*": this API's
# clients send credentials (a JWT or an API key, both carried in a header),
# and a wildcard origin cannot be combined with credentialed
# requests - the browser-enforced rule CORS relies on to stop a malicious
# page from reading another site's authenticated response breaks the
# instant "any origin" also means "and send its cookies/headers along".
CORS_ALLOWED_ORIGINS = [
    origin.strip() for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if origin.strip()
]
if "*" in CORS_ALLOWED_ORIGINS:
    raise ValueError(
        "CORS_ALLOWED_ORIGINS must not include '*' - name exact allowed origins "
        "instead. A wildcard origin cannot be combined with credentialed requests, "
        "which this API's clients send (see this constant's own comment)."
    )

# --- Service-to-service API keys ------------------------------------------
# Comma-separated static keys authenticating a machine caller (a script, a
# cron job, a partner integration) as the seeded demo user, as an
# alternative to logging in for a JWT - see auth/api_keys.py. An API key has
# no expiry and needs no login step, unlike an access token; that's the
# entire point (nothing here signs in on a schedule), and exactly why it
# must be a long, random, secret string rather than something a human
# memorizes. Empty by default: no key authenticates anything until one is
# explicitly configured.
SERVICE_API_KEYS = frozenset(
    key.strip() for key in os.getenv("SERVICE_API_KEYS", "").split(",") if key.strip()
)

# --- Rate limiting (token bucket) ------------------------------------------
# One bucket per authenticated identity (the User.id auth/api_keys.py's
# get_current_caller() resolves for either a JWT or an API key), guarding
# every /api/v1/reports route - see rate_limit/. A separate logical Redis DB
# from the Celery broker (0) and result backend (1), for the same reason
# those two are already split from each other above: a queue-inspection or
# FLUSHDB-style command scoped to one concern should never see, or wipe,
# another concern's keys, even though all three point at the same physical
# Redis server by default.
RATE_LIMIT_REDIS_URL = os.getenv("RATE_LIMIT_REDIS_URL", "redis://localhost:6379/2")
# The bucket's total capacity: the maximum burst one identity can spend all
# at once before being throttled. Refills continuously (not in discrete
# steps) at RATE_LIMIT_REFILL_PER_SECOND tokens/second, capped at this value
# - never handed back in one lump per window, unlike a fixed-window counter.
RATE_LIMIT_BUCKET_CAPACITY = int(os.getenv("RATE_LIMIT_BUCKET_CAPACITY", "20"))
RATE_LIMIT_REFILL_PER_SECOND = float(os.getenv("RATE_LIMIT_REFILL_PER_SECOND", "2.0"))
# TTL on the Redis key backing one identity's bucket. A full refill
# (capacity / refill_per_second) is already enough idle time for the bucket
# to be back at capacity anyway, so letting the key itself expire well after
# that is indistinguishable from keeping it forever - except it doesn't
# leave one key per caller sitting in Redis forever once they stop calling.
RATE_LIMIT_KEY_TTL_SECONDS = int(os.getenv("RATE_LIMIT_KEY_TTL_SECONDS", "600"))

DEBUG_MODE = get_env_bool("DEBUG_MODE")

if DEBUG_MODE:
    if GH_TOKEN:
        print(f"🔑 GH_TOKEN loaded (starts with: {GH_TOKEN[:5]})")
    else:
        print("⚠️ GH_TOKEN not set. Check your .env file if you intended to use it.")

# Master offline switch. When on, it implies every MOCK_* flag below regardless of
# their individual env vars, so one variable takes the whole app off the network.
OFFLINE_MODE = get_env_bool("OFFLINE_MODE")

MOCK_FINANCIAL_MODELER_AGENT_OUTPUT = OFFLINE_MODE or get_env_bool("MOCK_FINANCIAL_MODELER_AGENT_OUTPUT")
MOCK_INGEST_INPUT_AGENT_OUTPUT = OFFLINE_MODE or get_env_bool("MOCK_INGEST_INPUT_AGENT_OUTPUT")
MOCK_MARKET_DATA_AGENT_OUTPUT = OFFLINE_MODE or get_env_bool("MOCK_MARKET_DATA_AGENT_OUTPUT")
MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT = OFFLINE_MODE or get_env_bool("MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT")
MOCK_ZONING_LAW_AGENT_OUTPUT = OFFLINE_MODE or get_env_bool("MOCK_ZONING_LAW_AGENT_OUTPUT")

MOCK_MARKET_DATA_API = OFFLINE_MODE or get_env_bool("MOCK_MARKET_DATA_API")

# Mirrors the per-agent MOCK_*_AGENT_OUTPUT pattern above: short-circuits
# scripts.sync_knowledge_base.sync_azure_to_pinecone() to a deterministic,
# zero-network summary instead of touching Azure Blob Storage, OpenAI
# embeddings and Pinecone for real. OFFLINE_MODE implies this too, so a
# scheduled Beat run never costs money unless explicitly taken online.
MOCK_KNOWLEDGE_BASE_SYNC = OFFLINE_MODE or get_env_bool("MOCK_KNOWLEDGE_BASE_SYNC")
