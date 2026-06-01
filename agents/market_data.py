from langchain_core.messages import SystemMessage, AIMessage

from config import NodeName, config
from config.config import MOCK_MARKET_DATA_AGENT_OUTPUT
from config.llm import base_model
from schema.market_data import RealEstateGatewayModel, LLMMarketEvaluations
from schema.state import OverallGraphState, MarketDataAgentOutput
from services.market_data_gateway import RapidRealEstateMarketClient

from utils.logger import log_agent_header, log_agent_content, log_message, log_agent_footer
from utils.utils import print_model, load_mock_fixture

async def market_data_agent_node(state: OverallGraphState) -> dict:
    """
    Marked Data Agent: Fetches raw market dataset telemetry from the RapidAPI gateway client
    and applies an inline structured LLM classification overlay to determine
    inventory velocity and pricing variance risk factors.
    """
    await log_agent_header(NodeName.MARKET_DATA_AGENT, "⚙️ Node: Market Data Agent")

    # Guard Clause Check
    if MOCK_MARKET_DATA_AGENT_OUTPUT:
        return await _get_market_data_mock_llm_response()

    target_city = state.ingest_input.city if state.ingest_input else "Unknown City"

    gateway = RapidRealEstateMarketClient.get_client()

    api_metrics: RealEstateGatewayModel = await gateway.fetch_market_metrics(target_city)

    if config.DEBUG_MODE:
        await log_agent_content(NodeName.MARKET_DATA_AGENT, f"🔄Total listings {api_metrics.total_listings}")

    print_model(api_metrics)

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

    print_model(output_payload)

    await log_agent_content(NodeName.MARKET_DATA_AGENT, f"🔄 output_payload evaluations {output_payload.evaluations.pricing_dispersion_risk}")

    await log_agent_footer(NodeName.MARKET_DATA_AGENT)

    return {
        "market_data": output_payload,
        "messages": [
            AIMessage(
                content="Market Data Agent: Successfully scanned data feeds via live structured LLM lookup.",
                name=NodeName.MARKET_DATA_AGENT.value
            )
        ]
    }

async def _get_market_data_mock_llm_response() -> dict:
    """
    Loads a comprehensive mock static dataset from a JSON fixture file,
    instantiated securely through MarketDataAgentOutput for graph state compliance.
    """
    await log_agent_content(NodeName.MARKET_DATA_AGENT, "🔄 [MOCK] Market Data Agent: Using mock data")

    mock_payload = load_mock_fixture("mock_market_data_output_payload.json", MarketDataAgentOutput)

    await log_agent_footer(NodeName.MARKET_DATA_AGENT)

    return {
        "market_data": mock_payload,
        "messages": [
            AIMessage(
                content="[MOCK] Market Data Agent: Using mock market data",
                name=NodeName.MARKET_DATA_AGENT.value
            )
        ]
    }
