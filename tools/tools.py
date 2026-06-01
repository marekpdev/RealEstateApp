# tools
from typing import Optional, List
from langchain_core.tools import Tool
from langchain_experimental.utilities import PythonREPL

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
    },
    "openstreetmap_service": {
        "transport": "stdio",
        "command": "uvx",
        "args": ["osm-mcp-server"]
    },
    "wikipedia_service": {
        "transport": "stdio",
        "command": "uvx",
        "args": ["wikipedia-mcp"]
    }
}

class UnifiedMCPGateway:
    """Exposes production-ready dynamic tool loading contracts across multiple servers."""

    @staticmethod
    async def get_tools(
        server_ids: Optional[List[str]] = None,
        disallowed_tool_names: Optional[List[str]] = None
    ):
        """
        Dynamically discovers tools from specified MCP servers while using a denylist
        to filter out structurally problematic schemas.

        :param server_ids: List of server keys from MCP_SERVER_REGISTRY to connect to.
                          If None, connects to all registered servers.
        :param disallowed_tool_names: Optional list of tools to drop (e.g. ['suggest_meeting_point']).
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
            print("⚠️ No matching MCP servers found in selection registry.")
            return []

        # 2. Instantiate client with targeted configuration and namespace conflict protection
        # tool_name_prefix=True ensures tool collisions are avoided (e.g., openstreetmap_service_search)
        client = MultiServerMCPClient(config_to_use, tool_name_prefix=True)

        # 3. Directly await the tool discovery contract hook with error boundaries
        print(f"🔄 Interrogating MCP servers via stdio handshake: {list(config_to_use.keys())}...")
        try:
            all_discovered_tools = await client.get_tools()
        except Exception as e:
            print(f"❌ CRITICAL HANDSHAKE FAILURE: Client dropped connections. Error: {e}")
            return []

        if not all_discovered_tools:
            print("⚠️ Discovery transaction finished successfully, but zero tools were exposed.")
            return []

        # 4. Filter out problematic tools using the Denylist, then apply sanitization
        ignored_tools = set(disallowed_tool_names or [])
        processed_tools = []

        for tool in all_discovered_tools:
            # Check if tool is on the denylist
            # Note: If tool_name_prefix=True, check matches prefixed names (e.g., openstreetmap_service_suggest_meeting_point)
            if tool.name in ignored_tools or any(tool.name.endswith(f"_{banned}") for banned in ignored_tools):
                print(f"🚫 Denylist Hit: Dropping problematic tool schema -> {tool.name}")
                continue

            # Sanitize tool schemas for OpenAI compatibility
            if hasattr(tool, 'args_schema') and isinstance(tool.args_schema, dict):
                _sanitize_schema(tool.args_schema)
                _apply_token_optimizations(tool.name, tool.args_schema)

            processed_tools.append(tool)

        # Diagnostic Summary Log
        if processed_tools:
            print("\n" + "=" * 60)
            print(f"✅ DYNAMIC MCP DISCOVERY SUCCESS: {len(processed_tools)} tools loaded.")
            print("=" * 60)
            for idx, tool in enumerate(processed_tools, 1):
                print(f"  🛠️  Tool {idx}: {tool.name}")
            print("=" * 60 + "\n")

        return processed_tools

# Instantiate a local secure sandboxed Python executor
python_repl = PythonREPL()

repl_tool = Tool(
    name="python_repl",
    description="A Python shell. Use this to execute mathematical equations, compound interest formulas, "
                "or real estate metrics calculations. Input should be valid python code. "
                "Always print your final calculations so you can read the output.",
    func=python_repl.run,
)

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
    elif tool_name == 'find_amenities_nearby':
        if 'limit' in properties:
            properties['limit']['default'] = 15
            properties['limit']['maximum'] = 30
