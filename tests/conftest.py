import pytest
import asyncio
from unittest.mock import MagicMock, patch
from schema.state import OverallGraphState

@pytest.fixture
def mock_state():
    return OverallGraphState()

@pytest.fixture
def mock_llm():
    """Fixture to mock the base LLM model."""
    with patch("config.llm.base_model", autospec=True) as mock:
        # Mock with_structured_output to return itself or a mock as needed by tests
        mock.with_structured_output.return_value = mock
        yield mock

@pytest.fixture(scope="session")
def event_loop():
    """Force the pytest-asyncio event loop to be session-scoped."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
