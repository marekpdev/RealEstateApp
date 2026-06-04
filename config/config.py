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
if GH_TOKEN:
    print(f"Success! Key loaded. (Starts with: {GH_TOKEN[:5]})")
else:
    print("Error: API Key not found. Check your .env file location.")

MOCK_FINANCIAL_MODELER_AGENT_OUTPUT = get_env_bool("MOCK_FINANCIAL_MODELER_AGENT_OUTPUT")
MOCK_INGEST_INPUT_AGENT_OUTPUT = get_env_bool("MOCK_INGEST_INPUT_AGENT_OUTPUT")
MOCK_MARKET_DATA_AGENT_OUTPUT = get_env_bool("MOCK_MARKET_DATA_AGENT_OUTPUT")
MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT = get_env_bool("MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT")
MOCK_ZONING_LAW_AGENT_OUTPUT = get_env_bool("MOCK_ZONING_LAW_AGENT_OUTPUT")

MOCK_MARKET_DATA_API = get_env_bool("MOCK_MARKET_DATA_API")

DEBUG_MODE = get_env_bool("DEBUG_MODE")
