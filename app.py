from langchain_core.messages import HumanMessage
import chainlit as cl
from graph import compiledStateGraph

@cl.on_chat_start
async def on_chat_start():
    """Initializes the session when a recruiter opens the webpage."""
    await cl.Message(
        content="👋 **Real Estate Investment Planner Online**. Type your investment parameters to see the multi-agent graph route in real time!"
    ).send()

@cl.on_message
async def on_message(message: cl.Message):
    """
    Triggered every time a user types into the chatbox.
    This replaces your __main__ manual text query loops.
    """
    # 1. Map the user's UI prompt to your LangGraph shape
    inputs = {"messages": [HumanMessage(content=message.content)]}
    config = {"recursion_limit": 20}

    # 2. Run your state update streaming loop asynchronously
    async for output in compiledStateGraph.astream(inputs, config=config, stream_mode="updates"):

        for node_name, state_update in output.items():
            # Clean up the name format for visual polish in the UI drawer
            clean_node_title = node_name.replace("_", " ").title()

            # Create a brand-new collapsible step inside Chainlit's UI for this node
            node_step = cl.Step(name=f"⚙️ Node: {clean_node_title}")
            await node_step.send()

            # Initialize a safe base string variable for logs to prevent addition errors
            log_output = ""

            # 3. Handle messages inside the node update payload
            if "messages" in state_update and state_update["messages"]:
                last_msg = state_update["messages"][-1]

                # If the node generated a text breakdown (like financial reports or logs)
                if hasattr(last_msg, "content") and last_msg.content:
                    # Write the response directly inside the expanded node drawer logs
                    log_output = f"**Response:**\n{last_msg.content}"

                    # Special Rule: If this is the absolute final output node,
                    # write it directly to the main main viewport conversation path too!
                    if node_name == "financial_modeler_agent_node":
                        await cl.Message(content=last_msg.content).send()

                # If the node triggered structured tools (like SQL or Vector DB search queries)
                if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                    tools_used = [t['name'] for t in last_msg.tool_calls]
                    log_output += f"\n\n🛠️ **Triggered Tools:** `{', '.join(tools_used)}`"

            # Assign the complete string safely to the step output and push updates to UI
            node_step.output = log_output
            await node_step.update()