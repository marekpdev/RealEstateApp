import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from tools.tools import _sanitize_schema, _apply_token_optimizations, UnifiedMCPGateway

def test_sanitize_schema():
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "format": "uri"},
            "nested": {
                "properties": {
                     "link": {"type": "string", "format": "uri"}
                },
                "items": [{"type": "string", "format": "uri"}]
            }
        }
    }
    _sanitize_schema(schema)
    assert "format" not in schema["properties"]["url"]
    assert "format" not in schema["properties"]["nested"]["properties"]["link"]
    assert "format" not in schema["properties"]["nested"]["items"][0]

def test_apply_token_optimizations_fetch():
    schema = {
        "properties": {
            "max_length": {"default": 5000, "maximum": 20000, "exclusiveMaximum": True}
        }
    }
    _apply_token_optimizations("fetch", schema)
    assert schema["properties"]["max_length"]["default"] == 2000
    assert schema["properties"]["max_length"]["maximum"] == 10000
    assert "exclusiveMaximum" not in schema["properties"]["max_length"]

def test_apply_token_optimizations_brave():
    schema = {
        "properties": {
            "count": {"default": 10}
        }
    }
    _apply_token_optimizations("brave_web_search", schema)
    assert schema["properties"]["count"]["default"] == 3
    assert schema["properties"]["count"]["maximum"] == 5

@pytest.mark.asyncio
async def test_unified_mcp_gateway_get_tools_filtering():
    mock_tool = MagicMock()
    mock_tool.name = "server1_test_tool"
    mock_tool.args_schema = {"properties": {}}
    
    mock_banned_tool = MagicMock()
    mock_banned_tool.name = "server1_banned_tool"
    
    with patch("tools.tools.MultiServerMCPClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.get_tools = AsyncMock(return_value=[mock_tool, mock_banned_tool])
        
        # Test filtering with exact name (prefixed)
        tools = await UnifiedMCPGateway.get_tools(disallowed_tool_names=["server1_banned_tool"])
        assert len(tools) == 1
        assert tools[0].name == "server1_test_tool"

        # Test filtering with suffix match
        tools = await UnifiedMCPGateway.get_tools(disallowed_tool_names=["banned_tool"])
        assert len(tools) == 1
        assert tools[0].name == "server1_test_tool"

def test_apply_token_optimizations_prefixed_names():
    # Verify if prefixed names work (they probably DON'T currently)
    schema = {
        "properties": {
            "max_length": {"default": 5000}
        }
    }
    _apply_token_optimizations("fetch_service_fetch", schema)
    # This will fail if there's a bug
    assert schema["properties"]["max_length"]["default"] == 2000
