import importlib
from unittest.mock import patch

import pytest
import respx
from langchain_core.messages import HumanMessage

from config.safety import OfflineModeViolation, guarded_httpx_clients


def test_offline_mode_implies_all_mock_flags(monkeypatch):
    """OFFLINE_MODE=true must force every MOCK_* flag on, even if its own env var is unset."""
    monkeypatch.setenv("OFFLINE_MODE", "true")
    for var in [
        "MOCK_FINANCIAL_MODELER_AGENT_OUTPUT",
        "MOCK_INGEST_INPUT_AGENT_OUTPUT",
        "MOCK_MARKET_DATA_AGENT_OUTPUT",
        "MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT",
        "MOCK_ZONING_LAW_AGENT_OUTPUT",
        "MOCK_MARKET_DATA_API",
    ]:
        monkeypatch.delenv(var, raising=False)

    import config.config as config_module
    importlib.reload(config_module)
    try:
        assert config_module.OFFLINE_MODE is True
        assert config_module.MOCK_FINANCIAL_MODELER_AGENT_OUTPUT is True
        assert config_module.MOCK_INGEST_INPUT_AGENT_OUTPUT is True
        assert config_module.MOCK_MARKET_DATA_AGENT_OUTPUT is True
        assert config_module.MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT is True
        assert config_module.MOCK_ZONING_LAW_AGENT_OUTPUT is True
        assert config_module.MOCK_MARKET_DATA_API is True
    finally:
        monkeypatch.delenv("OFFLINE_MODE", raising=False)
        importlib.reload(config_module)


def test_llm_call_blocked_in_offline_mode():
    """A real call through the shared base_model must raise before any request leaves the process."""
    from config.llm import base_model

    with patch("config.config.OFFLINE_MODE", True):
        with pytest.raises(OfflineModeViolation):
            base_model.invoke([HumanMessage(content="hi")])


@pytest.mark.asyncio
async def test_base_api_client_blocked_in_offline_mode():
    from services.base_api_client import BaseAPIClient

    client = BaseAPIClient(base_url="https://api.test")
    with patch("config.config.OFFLINE_MODE", True):
        with pytest.raises(OfflineModeViolation):
            await client._send_request("GET", "/somewhere")


@pytest.mark.asyncio
async def test_compile_ui_log_offline_is_deterministic_and_network_free():
    from logger.lmm_translator import compile_ui_log, LogType

    with patch("logger.lmm_translator.OFFLINE_MODE", True):
        first = await compile_ui_log(LogType.NODE_START, "market_data_agent", {"foo": "bar"})
        second = await compile_ui_log(LogType.NODE_START, "market_data_agent", {"foo": "different"})

    assert first == second
    assert first.startswith("🔄")
    assert "Market Data Agent" in first


@pytest.mark.asyncio
async def test_guarded_client_passes_through_when_online():
    """The same transport must behave like a normal client once OFFLINE_MODE is off."""
    _, async_client = guarded_httpx_clients("test-caller")
    try:
        async with respx.mock:
            respx.get("https://example.test/ping").respond(json={"ok": True})
            response = await async_client.get("https://example.test/ping")
        assert response.json() == {"ok": True}
    finally:
        await async_client.aclose()


@pytest.mark.asyncio
async def test_guarded_client_blocks_when_offline():
    _, async_client = guarded_httpx_clients("test-caller")
    try:
        with patch("config.config.OFFLINE_MODE", True):
            with pytest.raises(OfflineModeViolation):
                await async_client.get("https://example.test/ping")
    finally:
        await async_client.aclose()
