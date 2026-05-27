# tools
from typing import Optional, List

from langchain_mcp_adapters.client import MultiServerMCPClient

from config import config

class UnifiedMCPGateway:
    """Exposes production-ready dynamic tool loading contracts across multiple servers."""

# TODO move it to enum
    @staticmethod
    async def get_tools(allowed_tool_names: Optional[List[str]] = None):
        # Establish the infrastructure configuration once
        async with MultiServerMCPClient({
            "brave_search": {
                "command": "uvx",
                "args": ["mcp-brave-search"],
                "env": {"BRAVE_API_KEY": config.BRAVE_API_KEY}
            },
            "fetch_service": {
                "command": "uvx",
                "args": ["mcp-server-fetch"]
            }
        }) as client:
            all_discovered_tools = await client.get_tools()

            # If no filter is passed, return everything safely
            if not allowed_tool_names:
                return all_discovered_tools

            # 🎯 Filter out only the tools matching our strict allowance list
            return [tool for tool in all_discovered_tools if tool.name in allowed_tool_names]