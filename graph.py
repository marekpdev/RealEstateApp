from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

import agents
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
from tools.tools import UnifiedMCPGateway

def get_next_path(state: OverallGraphState):
    """Checks if the agent wants to call a tool or is finished."""
    last_message = state.messages[-1]
    if last_message.tool_calls:
        return "tools"
    return "continue"

graph = StateGraph(OverallGraphState)


# TODO why there is await' needed
# TODO why we also here need to give allowed_tool_names if we already given them somewhere else
zoning_law_tools = await UnifiedMCPGateway.get_tools(allowed_tool_names=["brave_web_search", "fetch"])

# Register all nodes
graph.add_node(NodeName.INGEST_INPUT_AGENT, ingest_input_agent_node)
graph.add_node(NodeName.SUPERVISOR_AGENT, supervisor_agent_node)
graph.add_node(NodeName.MARKET_DATA_AGENT, market_data_agent_node)
graph.add_node(NodeName.NEIGHBORHOOD_VIBE_AGENT, neighborhood_vibe_agent_node)
graph.add_node(NodeName.ZONING_LAW_AGENT, zoning_law_agent_node)
graph.add_node(NodeName.ZONING_LAW_TOOL_NODE, ToolNode(zoning_law_tools))
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
graph.add_conditional_edges(
    NodeName.ZONING_LAW_AGENT,
    get_next_path,
{
        "tools": NodeName.ZONING_LAW_TOOL_NODE,
        "continue": END
    }
)
graph.add_edge(NodeName.ZONING_LAW_TOOL_NODE, NodeName.ZONING_LAW_AGENT)

# Connect the final synthesizer node out to the system termination block
graph.add_edge(NodeName.FINANCIAL_MODELER_AGENT, END)

graph.set_entry_point(NodeName.INGEST_INPUT_AGENT)

compiledStateGraph = graph.compile()
