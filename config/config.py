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
