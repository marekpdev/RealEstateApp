from enum import Enum

class NodeName(str, Enum):
    INGEST_INPUT_AGENT = "ingest_input_agent"
    SUPERVISOR_AGENT = "supervisor_agent"
    MARKET_DATA_AGENT = "market_data_agent"
    NEIGHBORHOOD_VIBE_AGENT = "neighborhood_vibe_agent"
    ZONING_LAW_AGENT = "zoning_law_agent"
    FINANCIAL_MODELER_AGENT = "financial_modeler_agent"

class LLMModelType(str, Enum):
    FAST_MODEL = "gpt-4o-mini"
    COMPLEX_MODEL ="gpt-4o"

class APIEndpoint(str, Enum):
    GITHUB_MODELS = "https://models.inference.ai.azure.com"