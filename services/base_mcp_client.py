# services/base_mcp_client.py
import os
import json
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import HTTPException
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from config import config
from utils.logger import log_agent_content

class BaseMCPClient:
    """
    A unified, flexible Client that spawns and executes commands against
    any Python-based MCP server via `uvx` over standard I/O (stdio).
    Supports offline snapshot test fixtures.
    """

    def __init__(self, server_package: str, env_vars: Optional[Dict[str, str]] = None):
        """
        Args:
            server_package: The name of the PyPI package (e.g., 'mcp-server-brave-search')
            env_vars: Dict of any secure keys/tokens needed by this specific server
        """
        self.server_package = server_package
        # Ensure child subprocess inherits system paths + custom API keys
        self.env = {**os.environ, **(env_vars or {})}

    async def call_tool(
            self,
            tool_name: str,
            arguments: Dict[str, Any],
            fixture_path: Optional[Path] = None,
            mock_external_api: bool = False
    ) -> str:
        """
        Discovers, connects, executes a tool on the target MCP server,
        and safely returns the raw text output response.
        """
        # 1. Local Simulation / Snapshot Fixture Hook
        if mock_external_api:
            if fixture_path and fixture_path.exists():
                if config.DEBUG_MODE:
                    await log_agent_content("BaseMCPClient",
                                            f"⚠️ [MOCK MCP ACTIVE] Loading static snapshot: {fixture_path.name}")
                return fixture_path.read_text(encoding="utf-8")
            raise HTTPException(
                status_code=500,
                detail=f"Simulation error: Missing MCP snapshot file at {fixture_path}"
            )

        # 2. Production Sandbox Execution Layer via uvx
        try:
            if config.DEBUG_MODE:
                await log_agent_content("BaseMCPClient",
                                        f"🚀 Spawning MCP server environment for '{self.server_package}'...")

            server_params = StdioServerParameters(
                command="uvx",
                args=[self.server_package],
                env=self.env
            )

            # Open standard I/O transport pipeline connection
            async with stdio_client(server_params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:

                    # Perform Protocol Handshake initialization
                    await session.initialize()

                    if config.DEBUG_MODE:
                        await log_agent_content("BaseMCPClient",
                                                f"⚙️ Dispatching execution request to tool: '{tool_name}'")

                    # Invoke the tool across the protocol boundary
                    mcp_response = await session.call_tool(name=tool_name, arguments=arguments)

                    if not mcp_response.content:
                        raise HTTPException(status_code=502, detail="MCP Server returned an empty content array.")

                    # Return the textual answer chunk
                    return mcp_response.content[0].text

        except Exception as exc:
            if config.DEBUG_MODE:
                await log_agent_content("BaseMCPClient", f"❌ MCP Protocol Exception Error: {str(exc)}")
            raise HTTPException(
                status_code=503,
                detail=f"MCP Subprocess communication infrastructure outage: {exc}"
            )