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
# request handler - app.py/cli.py enqueue the work, then poll the
# investment_requests row (the only channel both processes share right now)
# until it reaches a terminal status. poll_interval trades responsiveness
# for database load; timeout is a safety net so a caller never waits
# forever on a job whose worker died without updating the row.
REPORT_POLL_INTERVAL_SECONDS = float(os.getenv("REPORT_POLL_INTERVAL_SECONDS", "1.0"))
REPORT_POLL_TIMEOUT_SECONDS = float(os.getenv("REPORT_POLL_TIMEOUT_SECONDS", "300"))

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
