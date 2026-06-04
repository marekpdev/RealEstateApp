import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

class LogType(str, Enum):
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    NODE_START = "node_start"
    NODE_SUMMARY = "node_summary"
    GENERAL = "general"

# 1. Define the structural Pydantic contract for the UI output
class LogSummarySchema(BaseModel):
    ui_string: str = Field(
        description="A concise, sleek, human-readable UI message starting with a contextual emoji. Never use raw JSON syntax."
    )

# 2. Instantiate a dedicated, fast, low-latency compiler model
# (Using a high-speed, cost-effective model like gpt-4o-mini with structured output enforcement)
_log_summary_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0).with_structured_output(LogSummarySchema)

_PROMPTS = {
    LogType.NODE_START: ChatPromptTemplate.from_messages([
        ("system", (
            "You are a UX writer for a premium real estate investment platform.\n"
            "Generate a sleek, short message showing that a specific graph workflow node has just started executing.\n"
            "CRITICAL: Write in pure text. Do NOT use markdown (**, `, _) or HTML tags.\n"
            "Start with an appropriate active animation emoji (like 🔄, ⚡, 🔍, 🧭, 📈) matching the action.\n"
            "Keep it under 10 words. Focus on the action being initialized.\n"
            "Example: '🔄 Processing input prompt and extracting core criteria...'"
        )),
        ("human", "Node Name: {context_name}\nCurrent Input Parameters: {payload}")
    ]),

    LogType.NODE_SUMMARY: ChatPromptTemplate.from_messages([
        ("system", (
            "You are a UX writer for a premium real estate investment platform.\n"
            "Summarize what a specific workflow graph node just successfully accomplished.\n"
            "CRITICAL: Write in pure text. Do NOT use markdown (**, `, _) or HTML tags.\n"
            "Start with an appropriate definitive emoji. Put key metrics or entities inside single quotes.\n"
            "Example: '🎯 Successfully extracted target market as 'Miami, FL' with a budget ceiling of '$1,200,000'."
        )),
        ("human", "Node Name: {context_name}\nState Data: {payload}")
    ]),

    LogType.TOOL_START: ChatPromptTemplate.from_messages([
        ("system", (
            "You are a UX writer for a premium real estate platform's progress trail.\n"
            "Convert raw tool arguments into a single progress sentence starting with an emoji.\n"
            "CRITICAL: Write in pure text. Do NOT use markdown (**, `, _) or HTML tags.\n"
            "Place the core target, parameter, or query inside single quotes.\n"
            "Example: '🔍 Mapping local amenities near coordinates '34.0522, -118.2437'.'"
        )),
        ("human", "Tool Name: {context_name}\nArguments: {payload}")
    ]),

    LogType.TOOL_END: ChatPromptTemplate.from_messages([
        ("system", (
            "You are a UX writer for a premium real estate platform's progress trail.\n"
            "Summarize tool execution data rows into a single success sentence starting with an emoji.\n"
            "CRITICAL: Write in pure text. Do NOT use markdown (**, `, _) or HTML tags.\n"
            "Condense massive arrays into count summaries. Place key numbers/takeaways inside single quotes.\n"
            "Example: '✅ Successfully extracted '14 schools' and '2 post offices' from the regional dataset.'"
        )),
        ("human", "Tool Name: {context_name}\nOutput Content: {payload}")
    ]),

    LogType.GENERAL: ChatPromptTemplate.from_messages([
        ("system", (
            "You are a UX writer for an advanced AI dashboard.\n"
            "Convert this raw developer log or system notice into a sleek, clean, consumer-facing message starting with an emoji.\n"
            "CRITICAL: Pure text. No markdown, no HTML. Use single quotes for emphasis variables."
        )),
        ("human", "System Context: {context_name}\nLog Content: {payload}")
    ])
}


# =====================================================================
# 🚀 THE UNIFIED GENERATION WORKER
# =====================================================================

async def compile_ui_log(log_type: LogType, context_name: str, raw_payload: Any) -> str:
    """
    Core engine that handles routing raw logger through structured prompts using type-safe Enums.
    Returns a clean, ready-to-display UI string.
    """
    # Safeguard against invalid log types falling back safely
    if log_type not in _PROMPTS:
        log_type = LogType.GENERAL

    if isinstance(raw_payload, (dict, list)):
        payload_str = json.dumps(raw_payload)
    else:
        payload_str = str(raw_payload)

    payload_preview = payload_str[:4000]

    try:
        chain = _PROMPTS[log_type] | _log_summary_llm
        result = await chain.ainvoke({
            "context_name": context_name,
            "payload": payload_preview
        })
        return result.ui_string
    except Exception:
        clean_context = context_name.replace("_", " ").title()
        return f"⚙️ Operations updated successfully for {clean_context}."
