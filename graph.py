from langgraph.graph import StateGraph, START, END

from config import NodeName
from schema import OverallGraphState
from agents import (
    ingest_input_agent_node,
    supervisor_agent_node,
    market_data_agent_node,
    neighborhood_vibe_agent_node,
    zoning_law_agent_node,
    financial_modeler_agent_node,
)

graph = StateGraph(OverallGraphState)

# Register all nodes
graph.add_node(NodeName.INGEST_INPUT_AGENT, ingest_input_agent_node)
graph.add_node(NodeName.SUPERVISOR_AGENT, supervisor_agent_node)
graph.add_node(NodeName.MARKET_DATA_AGENT, market_data_agent_node)
graph.add_node(NodeName.NEIGHBORHOOD_VIBE_AGENT, neighborhood_vibe_agent_node)
graph.add_node(NodeName.ZONING_LAW_AGENT, zoning_law_agent_node)
graph.add_node(NodeName.FINANCIAL_MODELER_AGENT, financial_modeler_agent_node)

# Wire the sequential intake
graph.add_edge(START, NodeName.INGEST_INPUT_AGENT)
graph.add_edge(NodeName.INGEST_INPUT_AGENT, NodeName.SUPERVISOR_AGENT)

# PARALLEL FAN-OUT: Ingest branches into separate researcher tracks
graph.add_edge(NodeName.SUPERVISOR_AGENT, NodeName.MARKET_DATA_AGENT)
graph.add_edge(NodeName.SUPERVISOR_AGENT, NodeName.NEIGHBORHOOD_VIBE_AGENT)
graph.add_edge(NodeName.SUPERVISOR_AGENT, NodeName.ZONING_LAW_AGENT)

# PARALLEL FAN-IN
graph.add_edge(NodeName.MARKET_DATA_AGENT, NodeName.FINANCIAL_MODELER_AGENT)
graph.add_edge(NodeName.NEIGHBORHOOD_VIBE_AGENT, NodeName.FINANCIAL_MODELER_AGENT)
graph.add_edge(NodeName.ZONING_LAW_AGENT, NodeName.FINANCIAL_MODELER_AGENT)

# Connect the final synthesizer node out to the system termination block
graph.add_edge(NodeName.FINANCIAL_MODELER_AGENT, END)

graph.set_entry_point(NodeName.INGEST_INPUT_AGENT)

compiledStateGraph = graph.compile()
