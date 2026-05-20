from enum import Enum

class NodeName(str, Enum):
    INGEST_AGENT = "ingest_agent"
    SUPERVISOR_AGENT = "supervisor_agent"
    MARKET_DATA_AGENT = "market_data_agent"
    NEIGHBORHOOD_VIBE_AGENT = "neighborhood_vibe_agent"
    # ZONING_LAW_AGENT = "zoning_law_agent"
    FINANCIAL_MODELER = "financial_modeler_agent"