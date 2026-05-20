from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from config import NodeName
from config.llm import base_model
from schema.state import OverallGraphState, MarketDataAgentOutput

# Local control toggle for this specific node
use_mock = True

def market_data_agent_node(state: OverallGraphState) -> dict:
    """
    Market Data Agent: Coordinates database searches and real estate listings lookup.
    Granularly routes between mock records and a live retrieval/LLM tool pipeline.
    """
    # Guard Clause Check
    if use_mock:
        return _get_market_data_mock_response(state)

    # --- Live AI Reasoning Path ---
    # Step 1: Extract variables populated down one level inside the nested Ingest module
    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"
    max_budget = state.ingest_input.budget if state.ingest_input else "Unknown Budget"

    # Step 2: Bind your matching flat market data schema to force structured JSON wrapping
    structured_llm = base_model.with_structured_output(MarketDataAgentOutput)

    # Step 3: Query listings via the structured model pipeline
    extraction_result: MarketDataAgentOutput = structured_llm.invoke([
        SystemMessage(
            content=(
                "You are an expert real estate data analyst specializing in local market MLS data. "
                "Your job is to look up active properties, pricing tiers, and average cap rates "
                "matching the user's location and budget bounds. Provide a clean, structured string output."
            )
        ),
        HumanMessage(
            content=f"Find active real estate investment properties in '{target_city}' under a maximum budget of '{max_budget}'."
        )
    ])

    # Step 4: Return the validated Pydantic object directly under the state key
    return {
        "market_data": extraction_result,
        "messages": [
            AIMessage(
                content="Market Data Agent: Successfully scanned data feeds via live structured LLM lookup.",
                name=NodeName.MARKET_DATA_AGENT.value
            )
        ]
    }


# --- PRIVATELY SCORED MOCK PROVIDER ---
def _get_market_data_mock_response(state: OverallGraphState) -> dict:
    """
    Returns a static state-update payload mimicking a successful database query tool execution,
    instantiated securely through MarketDataAgentOutput for type-safety.
    """
    # Safely unfold parameters from the ingest layer for formatting the mock text
    target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"
    max_budget = state.ingest_input.budget if state.ingest_input else "Unknown Budget"

    mock_sql_result = (
        f"SQL Query Result for {target_city} (Max: {max_budget}):\n"
        "- 123 Brickell Ave: $580,000 | 2 Bed | 2 Bath | Cap Rate: 6.2%\n"
        "- 789 Wynwood Way: $520,000 | 1 Bed | 1.5 Bath | Cap Rate: 5.8%\n"
        "Market Trend: Median price per sq ft has expanded 4.5% year-over-year."
    )

    # Instantiate the typed output object explicitly to match the Graph state expectations
    mock_payload = MarketDataAgentOutput(
        market_data=mock_sql_result
    )

    return {
        "market_data": mock_payload,
        "messages": [
            AIMessage(
                content="[MOCK] Market Data Agent: Successfully scanned relational tables. Loaded matching listings into state.",
                name=NodeName.MARKET_DATA_AGENT.value
            )
        ]
    }