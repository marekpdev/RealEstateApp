from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from config import NodeName
from config.llm import base_model
from schema.state import OverallGraphState

# Local control toggle for this specific node
use_mock = True

def market_data_agent_node(state: OverallGraphState):
    """
    Market Data Agent: Coordinates database searches and real estate listings lookup.
    Granularly routes between mock records and a live retrieval/LLM tool pipeline.
    """
    # Guard Clause Check
    if use_mock:
        return _get_market_data_mock_response(state)

    # --- Live AI Reasoning Path ---
    # Step 1: Extract variables populated by the upstream Ingest node
    target_city = state.city
    max_budget = state.budget

    # Step 2: Query listings (Usually via an LLM tool binding or direct service call)
    # This is a placeholder showing how your live agent uses the state variables:
    ai_response = base_model.invoke([
        SystemMessage(
            content=(
                "You are an expert real estate data analyst specializing in local market MLS data. "
                "Your job is to look up active properties, pricing tiers, and average cap rates "
                "matching the user's location and budget bounds. Provide a clean, structured text output."
            )
        ),
        HumanMessage(
            content=f"Find active real estate investment properties in '{target_city}' under a maximum budget of '{max_budget}'."
        )
    ])

    # Step 3: Return the updated state payload with live listings
    return {
        "market_data": ai_response.content,
        "messages": [
            AIMessage(
                content="Market Data Agent: Successfully scanned data feeds via live LLM lookup.",
                name=NodeName.MARKET_DATA_AGENT.value
            )
        ]
    }


# --- PRIVATELY SCORED MOCK PROVIDER ---
def _get_market_data_mock_response(state: OverallGraphState) -> dict:
    """
    Returns a static state-update payload mimicking a
    successful database query tool execution.
    """
    mock_sql_result = (
        f"SQL Query Result for {state.city} (Max: {state.budget}):\n"
        "- 123 Brickell Ave: $580,000 | 2 Bed | 2 Bath | Cap Rate: 6.2%\n"
        "- 789 Wynwood Way: $520,000 | 1 Bed | 1.5 Bath | Cap Rate: 5.8%\n"
        "Market Trend: Median price per sq ft has expanded 4.5% year-over-year."
    )

    return {
        "market_data": mock_sql_result,
        "messages": [
            AIMessage(
                content="[MOCK] Market Data Agent: Successfully scanned relational tables. Loaded matching listings into state.",
                name=NodeName.MARKET_DATA_AGENT.value
            )
        ]
    }