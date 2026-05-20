from langgraph.graph import StateGraph, START, END

from NodeName import NodeName
from financial_modeler import mock_financial_modeler_node
from ingest import ingest_input_node
from market_data import mock_market_data_node
from neighborhood_vibe import mock_neighborhood_vibe_node
from state import OverallGraphState
from zoning_law import mock_zoning_law_node

graph = StateGraph(OverallGraphState)

# Register all nodes
graph.add_node(NodeName.INGEST_AGENT, ingest_input_node)
graph.add_node(NodeName.MARKET_DATA_AGENT, mock_market_data_node)
graph.add_node(NodeName.NEIGHBORHOOD_VIBE_AGENT, mock_neighborhood_vibe_node)
graph.add_node(NodeName.ZONING_LAW_AGENT, mock_zoning_law_node)
graph.add_node(NodeName.FINANCIAL_MODELER, mock_financial_modeler_node)

# Wire the sequential intake
graph.add_edge(START, NodeName.INGEST_AGENT)

# PARALLEL FAN-OUT: Ingest branches into separate researcher tracks
graph.add_edge(NodeName.INGEST_AGENT, NodeName.MARKET_DATA_AGENT)
graph.add_edge(NodeName.INGEST_AGENT, NodeName.NEIGHBORHOOD_VIBE_AGENT)
graph.add_edge(NodeName.INGEST_AGENT, NodeName.ZONING_LAW_AGENT)

# PARALLEL FAN-IN
graph.add_edge(NodeName.MARKET_DATA_AGENT, NodeName.FINANCIAL_MODELER)
graph.add_edge(NodeName.NEIGHBORHOOD_VIBE_AGENT, NodeName.FINANCIAL_MODELER)
graph.add_edge(NodeName.ZONING_LAW_AGENT, NodeName.FINANCIAL_MODELER)

# Connect the final synthesizer node out to the system termination block
graph.add_edge(NodeName.FINANCIAL_MODELER, END)

graph.set_entry_point(NodeName.INGEST_AGENT)

compiledStateGraph = graph.compile()
