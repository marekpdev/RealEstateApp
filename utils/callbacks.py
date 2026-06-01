from typing import Any, Dict
from langchain_core.callbacks import AsyncCallbackHandler
from utils.logger import log_agent_content

class ToolLoggingCallbackHandler(AsyncCallbackHandler):
    def __init__(self, node_name: Any):
        super().__init__()
        self.node_name = node_name

    async def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs: Any) -> Any:
        print(f"DEBUG log_tool_start {serialized.get('name', 'Unknown')}")
        await log_agent_content(
            self.node_name,
            f"🛠️ Tool '{serialized.get('name', 'Unknown')}' is being used. Args: {input_str}"
        )

    async def on_tool_end(self, output: Any, **kwargs: Any) -> Any:
        """
        Triggers automatically when an MCP tool finishes execution.
        Splits long outputs to prevent messy logs.
        """
        print(f"DEBUG log_tool_end")
        output_str = str(output)

        # Optional: Truncate what you print to the console so 50k tokens don't spam your terminal
        preview_limit = 1000
        if len(output_str) > preview_limit:
            preview = output_str[:preview_limit] + f"\n... [Truncated for preview, total size: {len(output_str)} chars] ..."
        else:
            preview = output_str

        await log_agent_content(
            self.node_name,
            f"📥 Tool Execution Complete. Output Response:\n{preview}"
        )
