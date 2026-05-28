from langgraph.graph import StateGraph, START, END
from config import NodeName
from schema import OverallGraphState
import agents

# =====================================================================
# 1. INITIALIZATION & REPOSITORIES
# =====================================================================
graph = StateGraph(OverallGraphState)

# Explicit mapping of graph node keys to their executing Python handlers
NODE_REGISTRY = {
    NodeName.INGEST_INPUT_AGENT: agents.ingest_input_agent_node,
    NodeName.SUPERVISOR_AGENT: agents.supervisor_agent_node,
    NodeName.MARKET_DATA_AGENT: agents.market_data_agent_node,
    NodeName.NEIGHBORHOOD_VIBE_AGENT: agents.neighborhood_vibe_agent_node,
    NodeName.ZONING_LAW_AGENT: agents.zoning_law_agent_node,
    NodeName.FINANCIAL_MODELER_AGENT: agents.financial_modeler_agent_node,
}

# Group the worker nodes executing concurrently
RESEARCHER_NODES = [
    NodeName.MARKET_DATA_AGENT,
    NodeName.NEIGHBORHOOD_VIBE_AGENT,
    NodeName.ZONING_LAW_AGENT
]

# Register all nodes dynamically via a single clean loop
for node_name, node_func in NODE_REGISTRY.items():
    graph.add_node(node_name, node_func)

# =====================================================================
# 2. STREAMLINED TOPOLOGY DATA FLOW
# =====================================================================

# Linear Intake Sequence
graph.add_edge(START, NodeName.INGEST_INPUT_AGENT)
graph.add_edge(NodeName.INGEST_INPUT_AGENT, NodeName.SUPERVISOR_AGENT)

# 🔀 PARALLEL FAN-OUT: Map supervisor straight into the worker node list
for researcher in RESEARCHER_NODES:
    graph.add_edge(NodeName.SUPERVISOR_AGENT, researcher)

# 🤝 PARALLEL FAN-IN (Join): Route all workers directly into the downstream synthesizer
for researcher in RESEARCHER_NODES:
    graph.add_edge(researcher, NodeName.FINANCIAL_MODELER_AGENT)

# Execution complete
graph.add_edge(NodeName.FINANCIAL_MODELER_AGENT, END)

# =====================================================================
# 3. COMPILATION
# =====================================================================
compiledStateGraph = graph.compile()