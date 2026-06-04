import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from schema.state import (
    OverallGraphState, 
    IngestInputAgentOutput, 
    FinancialModelerAgentOutput,
    NeighborhoodVibeAgentOutput,
    ZoningLawAgentOutput
)
from schema.market_data import (
    RealEstateGatewayModel, 
    LLMMarketEvaluations, 
    PropertyRecord,
    MarketDataAgentOutput
)
from agents.supervisor import supervisor_agent_node
from agents.ingest_input import ingest_input_agent_node
from agents.market_data import market_data_agent_node
from agents.financial_modeler import financial_modeler_agent_node
from agents.neighborhood_vibe import neighborhood_vibe_agent_node
from agents.zoning_law import zoning_law_agent_node
from langchain_core.messages import HumanMessage

@pytest.mark.asyncio
async def test_supervisor_agent_node(mock_state):
    mock_state.ingest_input = IngestInputAgentOutput(city="Austin, TX", budget="$1M")
    result = await supervisor_agent_node(mock_state)
    assert "messages" in result
    assert "Austin, TX" in result["messages"][0].content
    assert "$1M" in result["messages"][0].content

@pytest.mark.asyncio
async def test_ingest_input_agent_node(mock_state, mock_llm):
    mock_state.messages = [HumanMessage(content="I want to buy a house in Austin for 1M")]
    
    mock_output = IngestInputAgentOutput(city="Austin, TX", budget="$1,000,000")
    
    mock_structured_llm = MagicMock()
    mock_structured_llm.invoke.return_value = mock_output
    
    # Patch base_model in the specific agent module where it's already imported
    with patch("agents.ingest_input.base_model") as mock_base:
        mock_base.with_structured_output.return_value = mock_structured_llm
        
        with patch("agents.ingest_input.MOCK_INGEST_INPUT_AGENT_OUTPUT", False):
            result = await ingest_input_agent_node(mock_state)
            
    assert result["ingest_input"] == mock_output
    assert "Austin, TX" in result["messages"][0].content

@pytest.mark.asyncio
async def test_market_data_agent_node(mock_state, mock_llm):
    mock_state.ingest_input = IngestInputAgentOutput(city="Austin, TX", budget="$1M")
    
    mock_api_metrics = RealEstateGatewayModel(
        requested_location="Austin, TX",
        total_listings=10,
        average_price=500000,
        median_price=450000,
        lowest_listing=100000,
        highest_listing=1000000,
        raw_properties=[]
    )
    
    mock_gateway = MagicMock()
    mock_gateway.fetch_market_metrics = AsyncMock(return_value=mock_api_metrics)
    
    mock_ai_evaluations = LLMMarketEvaluations(
        inventory_velocity_tier="BALANCED",
        pricing_dispersion_risk="MODERATE"
    )
    
    mock_structured_llm = MagicMock()
    mock_structured_llm.ainvoke = AsyncMock(return_value=mock_ai_evaluations)
    
    with patch("agents.market_data.base_model") as mock_base:
        mock_base.with_structured_output.return_value = mock_structured_llm
        
        with patch("services.market_data_gateway.RapidRealEstateMarketClient.get_client", return_value=mock_gateway):
            with patch("agents.market_data.MOCK_MARKET_DATA_AGENT_OUTPUT", False):
                result = await market_data_agent_node(mock_state)
            
    assert "market_data" in result
    assert result["market_data"].telemetry == mock_api_metrics
    assert result["market_data"].evaluations == mock_ai_evaluations

@pytest.mark.asyncio
async def test_financial_modeler_agent_node(mock_state):
    mock_state.ingest_input = IngestInputAgentOutput(city="Austin, TX", budget="$1M")
    
    mock_agent = MagicMock()
    mock_report = FinancialModelerAgentOutput(financial_report="Mock Report")
    mock_agent.ainvoke = AsyncMock(return_value={"structured_response": mock_report})
    
    with patch("agents.financial_modeler._get_compiled_financial_modeler_agent", new_callable=AsyncMock) as mock_get_agent:
        mock_get_agent.return_value = mock_agent
        with patch("agents.financial_modeler.MOCK_FINANCIAL_MODELER_AGENT_OUTPUT", False):
            with patch("agents.financial_modeler.render_financial_report", new_callable=AsyncMock):
                result = await financial_modeler_agent_node(mock_state)
                
    assert result["financial_report"] == mock_report

@pytest.mark.asyncio
async def test_neighborhood_vibe_agent_node(mock_state):
    mock_state.ingest_input = IngestInputAgentOutput(city="Austin, TX", budget="$1M")
    
    mock_agent = MagicMock()
    mock_output = NeighborhoodVibeAgentOutput(neighborhood_vibe="Great vibe")
    mock_agent.ainvoke = AsyncMock(return_value={"structured_response": mock_output})
    
    with patch("agents.neighborhood_vibe._get_compiled_neighborhood_vibe_agent", new_callable=AsyncMock) as mock_get_agent:
        mock_get_agent.return_value = mock_agent
        with patch("agents.neighborhood_vibe.MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT", False):
            result = await neighborhood_vibe_agent_node(mock_state)
                
    assert result["neighborhood_vibe"] == mock_output

@pytest.mark.asyncio
async def test_zoning_law_agent_node(mock_state):
    mock_state.ingest_input = IngestInputAgentOutput(city="Austin, TX", budget="$1M")
    
    mock_agent = MagicMock()
    mock_output = ZoningLawAgentOutput(zoning_laws="Strict zoning")
    mock_agent.ainvoke = AsyncMock(return_value={"structured_response": mock_output})
    
    with patch("agents.zoning_law._get_compiled_zoning_agent", new_callable=AsyncMock) as mock_get_agent:
        mock_get_agent.return_value = mock_agent
        with patch("agents.zoning_law.MOCK_ZONING_LAW_AGENT_OUTPUT", False):
            result = await zoning_law_agent_node(mock_state)
                
    assert result["zoning_laws"] == mock_output
