from pathlib import Path
import os

from services.base_mcp_client import BaseMCPClient

class BraveSearchMCPClient:
    """Wrapper encapsulating the Brave Search MCP connection specifics."""
    @staticmethod
    def get_client() -> BaseMCPClient:
        return BaseMCPClient(
            server_package="mcp-server-brave-search",
            env_vars={"BRAVE_API_KEY": os.environ.get("BRAVE_API_KEY", "")}
        )