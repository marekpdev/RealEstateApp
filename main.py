import asyncio
from langchain_core.messages import HumanMessage
from graph import compiledStateGraph

async def main():
    """Asynchronous orchestrator for terminal-based LangGraph testing."""
    # query = input("Ask the Agent: ")
    query = "I would like to invest in Los Angeles, CA, and my max budget is $800,000"

    # Inputs mapped to your global LangGraph schema shape
    inputs = {"messages": [HumanMessage(content=query)]}
    config = {"recursion_limit": 20}

    print("🚀 Booting LangGraph local runtime... Invoking async stream loop.")

    # 🎯 FIX 1: Use 'async for' instead of standard 'for' when consuming .astream()
    async for output in compiledStateGraph.astream(inputs, config=config, stream_mode="updates"):

        for node_name, state_update in output.items():
            print(f"\n--- ⚙️ Node: {node_name} ---")

            # Safely check if messages array exists in this specific node state patch
            if "messages" in state_update and state_update["messages"]:
                last_msg = state_update["messages"][-1]

                # Print text breakdown answers from agents
                if hasattr(last_msg, "content") and last_msg.content:
                    print(f"Response: {last_msg.content}")

                # Print structured external function tool triggers
                if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                    tools = [t['name'] for t in last_msg.tool_calls]
                    print(f"Tool Calls: {tools}")


if __name__ == "__main__":
    asyncio.run(main())