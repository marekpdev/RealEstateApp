from langchain_core.messages import AIMessage

from state import OverallGraphState

def supervisor_node(state: OverallGraphState):
    """
        Deterministic Supervisor: Acts as a structural traffic controller.
        No LLM call is required here because the routing path is static.
        """
    target_city = state.city
    max_budget = state.budget
    return {
        "messages": [
            AIMessage(
                content=f"Supervisor: Parallel research initialized for {state.city} and budget {state.budget}",
                name="SupervisorNode"
            )
        ]
    }


    