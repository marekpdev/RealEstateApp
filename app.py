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

    await compiledStateGraph.ainvoke(inputs, config=config)