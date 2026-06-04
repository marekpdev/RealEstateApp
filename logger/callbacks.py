from logger.lmm_translator import compile_ui_log, LogType
from logger.logger import log_agent_content
import asyncio
from typing import Any, Dict
from langchain_core.callbacks import AsyncCallbackHandler


class ToolLoggingCallbackHandler(AsyncCallbackHandler):
    def __init__(self, node_name: Any):
        super().__init__()
        self.node_name = node_name
        self._current_tool_name = "Unknown"

    async def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs: Any) -> Any:
        self._current_tool_name = serialized.get("name", "Unknown")

        async def run_start():
            text = await compile_ui_log(LogType.TOOL_START, self._current_tool_name, input_str)
            await log_agent_content(self.node_name, text)

        asyncio.create_task(run_start())

    async def on_tool_end(self, output: Any, **kwargs: Any):
        async def run_end():
            text = await compile_ui_log(LogType.TOOL_END, self._current_tool_name, output)
            await log_agent_content(self.node_name, text)

        asyncio.create_task(run_end())
