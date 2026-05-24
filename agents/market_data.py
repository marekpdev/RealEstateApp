from langchain_core.messages import SystemMessage, AIMessage

from config import NodeName
from config.llm import base_model
from schema.market_data import RealEstateGatewayModel, LLMMarketEvaluations
from schema.state import OverallGraphState, MarketDataAgentOutput
from services.market_data_gateway import RapidRealEstateMarketClient
from pathlib import Path
from utils import print_model

# Local control toggle for this specific node
use_mock_llm_response = False

async def market_data_agent_node(state: OverallGraphState) -> dict:
    """
    Fetches raw market dataset telemetry from the RapidAPI gateway client
    and applies an inline structured LLM classification overlay to determine
    inventory velocity and pricing variance risk factors.
    """
    # Guard Clause Check
    if use_mock_llm_response:
        return _get_market_data_mock_llm_response()

    print(f"market_data_agent_node1")

    target_city = state.ingest_input.city if state.ingest_input else "Unknown City"

    print(f"market_data_agent_node2 {target_city}")

    gateway = RapidRealEstateMarketClient.get_client()

    api_metrics: RealEstateGatewayModel = await gateway.fetch_market_metrics(target_city)

    print_model(api_metrics, "market_data_agent_node3")

    structured_llm = base_model.with_structured_output(LLMMarketEvaluations)

    prompt = (
        "You are an automated real estate underwriting system. Analyze the raw market data "
        "provided below and accurately classify the inventory_velocity_tier and pricing_dispersion_risk.\n\n"
        f"Target Location: {api_metrics.requested_location}\n"
        f"Total Active Listings: {api_metrics.total_listings}\n"
        f"Calculated Average: ${api_metrics.average_price:,.2f}\n"
        f"Calculated Median: ${api_metrics.median_price:,.2f}\n"
        f"Absolute Range Spread: ${api_metrics.lowest_listing:,.2f} to ${api_metrics.highest_listing:,.2f}\n\n"
        "CLASSIFICATION RULES:\n"
        "- inventory_velocity_tier: Count total active listings. If under 5, classify as 'RUSH'. "
        "If between 5 and 50, classify as 'BALANCED'. If greater than 50, classify as 'STAGNANT'.\n"
        "- pricing_dispersion_risk: Calculate spread ratio (highest_listing divided by lowest_listing). "
        "If highest listing is more than 3x the lowest, classify as 'HIGH_VOLATILITY'. "
        "If less than 1.5x, classify as 'STABLE'. Otherwise, classify as 'MODERATE'."
    )

    # Step 3: Query listings via the structured model pipeline
    ai_evaluations: LLMMarketEvaluations = await structured_llm.ainvoke([SystemMessage(content=prompt)])

    output_payload = MarketDataAgentOutput(
        telemetry=api_metrics,  # Pure API data model
        evaluations=ai_evaluations  # Pure LLM evaluation model
    )

    print_model(output_payload, "market_data_agent_node4")

    return {
        "market_data": output_payload,
        "messages": [
            AIMessage(
                content="Market Data Agent: Successfully scanned data feeds via live structured LLM lookup.",
                name=NodeName.MARKET_DATA_AGENT.value
            )
        ]
    }

def _get_market_data_mock_llm_response() -> dict:
    """
    Loads a comprehensive mock static dataset from a JSON fixture file,
    instantiated securely through MarketDataAgentOutput for graph state compliance.
    """
    fixture_path = Path(__file__).parent.parent / "tests" / "fixtures" / "mock_market_data_output_payload.json"

    if not fixture_path.exists():
        raise FileNotFoundError(
            f"Mock configuration failure: The target JSON file was not found at {fixture_path}"
        )

    try:
        raw_json_content = fixture_path.read_text(encoding="utf-8")
        mock_payload = MarketDataAgentOutput.model_validate_json(raw_json_content)

    except Exception as e:
        raise ValueError(
            f"Data Integrity Fault: Failed to validate mock JSON structure into MarketDataAgentOutput. Error: {e}"
        )

    return {
        "market_data": mock_payload,
        "messages": [
            AIMessage(
                content="[MOCK] Market Data Agent: Using mock market data",
                name=NodeName.MARKET_DATA_AGENT.value
            )
        ]
    }