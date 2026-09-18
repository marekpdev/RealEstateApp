import json
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from config import LLMModelType
from config.config import OFFLINE_MODE, OPENAI_API_KEY
from config.safety import guarded_httpx_clients

class LogType(str, Enum):
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    NODE_START = "node_start"
    NODE_SUMMARY = "node_summary"
    GENERAL = "general"

class LogSummarySchema(BaseModel):
    ui_string: str = Field(
        description="A concise, sleek, human-readable UI message starting with a contextual emoji. Never use raw JSON syntax."
    )

_http_client, _http_async_client = guarded_httpx_clients("logger.lmm_translator._log_summary_llm")

_log_summary_llm = ChatOpenAI(
    model=LLMModelType.FAST_MODEL.value,
    temperature=0,
    api_key=OPENAI_API_KEY or "sk-offline-mode-placeholder",
    http_client=_http_client,
    http_async_client=_http_async_client,
    http_socket_options=(),
).with_structured_output(LogSummarySchema)

_OFFLINE_EMOJI = {
    LogType.NODE_START: "🔄",
    LogType.NODE_SUMMARY: "🎯",
    LogType.TOOL_START: "🔍",
    LogType.TOOL_END: "✅",
    LogType.GENERAL: "⚙️",
}


def _compile_ui_log_offline(log_type: LogType, context_name: str) -> str:
    """
    Deterministic stand-in for the LLM-written UI string. Keeps the same shape
    (starts with an emoji, one short line) so the UI is unaffected, without spending
    a real request per log line (see §6.8 in the learning roadmap).
    """
    emoji = _OFFLINE_EMOJI.get(log_type, "⚙️")
    clean_context = context_name.replace("_", " ").title()
    return f"{emoji} [offline] {clean_context}: {log_type.value.replace('_', ' ')}."

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


async def compile_ui_log(log_type: LogType, context_name: str, raw_payload: Any) -> str:
    """
    Core engine that handles routing raw logger through structured prompts using type-safe Enums.
    Returns a clean, ready-to-display UI string.
    """
    if log_type not in _PROMPTS:
        log_type = LogType.GENERAL

    if OFFLINE_MODE:
        return _compile_ui_log_offline(log_type, context_name)

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
