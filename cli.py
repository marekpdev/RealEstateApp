import asyncio
from langchain_core.messages import HumanMessage
from graph import compiledStateGraph

async def run_cli_pipeline():
    print("🚀 Starting App in CLI Mode...")

    """Asynchronous orchestrator for terminal-based LangGraph testing."""
    # query = input("Ask the Agent: ")
    query = "I would like to invest in Los Angeles, CA, and my max budget is $800,000"

    # Inputs mapped to your global LangGraph schema shape
    inputs = {"messages": [HumanMessage(content=query)]}
    config = {"recursion_limit": 20}

    await compiledStateGraph.ainvoke(inputs, config=config)

    print("\n🏁 App Finished Successfully.")

if __name__ == "__main__":
    asyncio.run(run_cli_pipeline())