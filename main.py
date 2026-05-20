from langchain_core.messages import HumanMessage

from graph import compiledStateGraph

if __name__ == "__main__":
    query = input("Ask the Agent: ")
    # query = "I would like to invest in Miami, FL, and my max budget is $200,000"

    # LangGraph handles the loop automatically
    inputs = {"messages": [HumanMessage(content=query)]}

    for output in compiledStateGraph.stream(inputs, config={"recursion_limit": 20}):
        for node_name, state_update in output.items():
            print(f"\n--- Node: {node_name} ---")
            last_msg = state_update["messages"][-1]
            if hasattr(last_msg, "content") and last_msg.content:
                print(f"Response: {last_msg.content}")
            if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                print(f"Tool Calls: {[t['name'] for t in last_msg.tool_calls]}")