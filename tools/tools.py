# tools
from typing import Optional, List

from langchain_mcp_adapters.client import MultiServerMCPClient

from config import config

MCP_SERVER_REGISTRY = {
    "brave_search": {
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-brave-search"],
        "env": {"BRAVE_API_KEY": config.BRAVE_API_KEY}
    },
    "fetch_service": {
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-fetch"]
    }
}

class UnifiedMCPGateway:
    """Exposes production-ready dynamic tool loading contracts across multiple servers."""

    @staticmethod
    async def get_tools(
        server_ids: Optional[List[str]] = None,
        allowed_tool_names: Optional[List[str]] = None
    ):
        """
        Dynamically discovers tools from specified MCP servers.

        :param server_ids: List of server keys from MCP_SERVER_REGISTRY to connect to.
                          If None, connects to all registered servers.
        :param allowed_tool_names: Optional strict whitelist of tool names to return.
        """
        # 1. Filter the registry to only include requested servers
        if server_ids is None:
            config_to_use = MCP_SERVER_REGISTRY
        else:
            config_to_use = {
                sid: MCP_SERVER_REGISTRY[sid]
                for sid in server_ids
                if sid in MCP_SERVER_REGISTRY
            }

        if not config_to_use:
            return []

        # 2. Instantiate the client with the targeted configuration
        client = MultiServerMCPClient(config_to_use)

        # 3. Directly await the tool discovery contract hook
        all_discovered_tools = await client.get_tools()

        # 4. Sanitize tool schemas for OpenAI compatibility (e.g., remove 'format': 'uri')
        for tool in all_discovered_tools:
            if hasattr(tool, 'args_schema') and isinstance(tool.args_schema, dict):
                _sanitize_schema(tool.args_schema)
                _apply_token_optimizations(tool.name, tool.args_schema)

        # 5. Apply optional tool-level filtering
        if not allowed_tool_names:
            return all_discovered_tools

        return [tool for tool in all_discovered_tools if tool.name in allowed_tool_names]

def _sanitize_schema(schema: dict):
    """Recursively removes unsupported JSON schema fields like 'format': 'uri'."""
    if not isinstance(schema, dict):
        return

    if 'format' in schema and schema['format'] == 'uri':
        del schema['format']

    for value in schema.values():
        if isinstance(value, dict):
            _sanitize_schema(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _sanitize_schema(item)

def _apply_token_optimizations(tool_name: str, schema: dict):
    """Injects more restrictive defaults to prevent token overflow on small-context models."""
    properties = schema.get('properties', {})

    if tool_name == 'fetch':
        if 'max_length' in properties:
            # Lowering default from 5000 to 2000 characters
            properties['max_length']['default'] = 2000
            # Capping max length to 10k to prevent LLM from requesting massive dumps
            properties['max_length']['maximum'] = 10000
            if 'exclusiveMaximum' in properties['max_length']:
                del properties['max_length']['exclusiveMaximum']

    elif tool_name == 'brave_web_search':
        if 'count' in properties:
            # Lowering default from 10 to 4 results
            properties['count']['default'] = 3
            # Capping count to 10
            properties['count']['maximum'] = 5