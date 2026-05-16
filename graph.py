from langgraph.graph import StateGraph, END

from NodeName import NodeName
from ingest import ingest_input_node
from state import OverallGraphState

graph = StateGraph(OverallGraphState)
graph.add_node(NodeName.INGEST_AGENT, ingest_input_node)
graph.set_entry_point(NodeName.INGEST_AGENT)
graph.add_edge(NodeName.INGEST_AGENT, END)
compiledStateGraph = graph.compile()
