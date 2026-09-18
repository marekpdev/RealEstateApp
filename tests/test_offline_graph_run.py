from unittest.mock import patch

import pytest
import respx
from langchain_core.messages import HumanMessage

from graph import compiledStateGraph


@pytest.mark.asyncio
async def test_full_graph_run_offline_zero_network_calls():
    """
    With every agent mock flag on and the UI log translator offline, a full run of
    the compiled graph must finish end to end without any real network call. respx
    is left with zero routes registered, so any httpx request that isn't intercepted
    by a mock branch raises instead of silently reaching the network.
    """
    with patch("agents.ingest_input.MOCK_INGEST_INPUT_AGENT_OUTPUT", True), \
         patch("agents.market_data.MOCK_MARKET_DATA_AGENT_OUTPUT", True), \
         patch("agents.neighborhood_vibe.MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT", True), \
         patch("agents.zoning_law.MOCK_ZONING_LAW_AGENT_OUTPUT", True), \
         patch("agents.financial_modeler.MOCK_FINANCIAL_MODELER_AGENT_OUTPUT", True), \
         patch("logger.lmm_translator.OFFLINE_MODE", True):

        async with respx.mock:
            inputs = {"messages": [HumanMessage(content="Invest in Austin, TX up to $900,000")]}
            result = await compiledStateGraph.ainvoke(inputs, config={"recursion_limit": 20})

    assert result["ingest_input"] is not None
    assert result["market_data"] is not None
    assert result["neighborhood_vibe"] is not None
    assert result["zoning_laws"] is not None
    assert result["financial_report"] is not None
