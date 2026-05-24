from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from config import NodeName
from config.llm import base_model
from schema.market_data import RealEstateGatewayModel, LLMMarketEvaluations
from schema.state import OverallGraphState, MarketDataAgentOutput
from services.market_data_gateway import RapidRealEstateMarketClient

# Local control toggle for this specific node

use_mock_llm_response = True

# TODO needs to be changed?
# Instantiate your service layer using its default fallback pool (safe for local console execution)
internal_gateway_service = RapidRealEstateMarketClient()

async def market_data_agent_node(state: OverallGraphState) -> dict:
    """
    Fetches raw market dataset telemetry from the RapidAPI gateway client
    and applies an inline structured LLM classification overlay to determine
    inventory velocity and pricing variance risk factors.
    """
    # Guard Clause Check
    # if use_mock_llm_response:
    #     return _get_market_data_mock_llm_response(state)

    print(f"market_data_agent_node1")

    target_city = state.ingest_input.city if state.ingest_input else "Unknown City"

    print(f"market_data_agent_node2 {target_city}")

    gateway = RapidRealEstateMarketClient.get_client()

    api_metrics: RealEstateGatewayModel = await gateway.fetch_market_metrics(target_city)

    print(f"market_data_agent_node3 {api_metrics}")

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

    print(f"market_data_agent_node4 {ai_evaluations}")

    output_payload = MarketDataAgentOutput(
        telemetry=api_metrics,  # Pure API data model
        evaluations=ai_evaluations  # Pure LLM evaluation model
    )

    return {
        "market_data": output_payload,
        "messages": [
            AIMessage(
                content="Market Data Agent: Successfully scanned data feeds via live structured LLM lookup.",
                name=NodeName.MARKET_DATA_AGENT.value
            )
        ]
    }


# --- PRIVATELY SCORED MOCK PROVIDER ---
# def _get_market_data_mock_llm_response(state: OverallGraphState) -> dict:
#     """
#     Returns a static state-update payload mimicking a successful database query tool execution,
#     instantiated securely through MarketDataAgentOutput for type-safety.
#     """
#     # Safely unfold parameters from the ingest layer for formatting the mock text
#     target_city = state.ingest_input.city if state.ingest_input else "Unknown Market"
#     max_budget = state.ingest_input.budget if state.ingest_input else "Unknown Budget"
#
#     mock_sql_result = (
#         f"SQL Query Result for {target_city} (Max: {max_budget}):\n"
#         "- 123 Brickell Ave: $580,000 | 2 Bed | 2 Bath | Cap Rate: 6.2%\n"
#         "- 789 Wynwood Way: $520,000 | 1 Bed | 1.5 Bath | Cap Rate: 5.8%\n"
#         "Market Trend: Median price per sq ft has expanded 4.5% year-over-year."
#     )
#
#     # Instantiate the typed output object explicitly to match the Graph state expectations
#     mock_payload = MarketDataAgentOutput(
#         market_data=mock_sql_result
#     )
#
#     return {
#         "market_data": mock_payload,
#         "messages": [
#             AIMessage(
#                 content="[MOCK] Market Data Agent: Successfully scanned relational tables. Loaded matching listings into state.",
#                 name=NodeName.MARKET_DATA_AGENT.value
#             )
#         ]
#     }