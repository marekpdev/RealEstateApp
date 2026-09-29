"""Progress steps shown in the chat UI: agents that run at the same time must
each be their own top-level step, never nested inside one another."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from logger import logger


class _FakeSession:
    def __init__(self):
        self._data = {}

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value):
        self._data[key] = value


@pytest.fixture
def fake_chainlit():
    session = _FakeSession()
    created = []

    def make_step(**kwargs):
        step = MagicMock(name=kwargs.get("name"))
        step.name = kwargs.get("name")
        step.parent_id = kwargs.get("parent_id")
        step.send = AsyncMock()
        step.update = AsyncMock()
        step.__aenter__ = AsyncMock()
        step.__aexit__ = AsyncMock()
        created.append(step)
        return step

    with patch.object(logger, "_is_chainlit_active", return_value=True), \
         patch.object(logger.cl, "user_session", session), \
         patch.object(logger.cl, "Step", side_effect=make_step):
        yield created


@pytest.mark.asyncio
async def test_overlapping_agents_are_independent_top_level_steps(fake_chainlit):
    await logger.log_agent_header("market_data_agent", "Node: Market Data Agent")
    await logger.log_agent_header("neighborhood_vibe_agent", "Node: Neighborhood Vibe Agent")
    await logger.log_agent_header("zoning_law_agent", "Node: Zoning Law Agent")

    assert len(fake_chainlit) == 3
    for step in fake_chainlit:
        # Never entered as a context manager (that is what makes a step
        # become the parent of whichever step opens next).
        step.__aenter__.assert_not_awaited()
        step.send.assert_awaited_once()
        assert step.output == "⏳ Running..."


@pytest.mark.asyncio
async def test_footer_closes_only_its_own_step_and_shows_a_result(fake_chainlit):
    await logger.log_agent_header("a", "Node: A")
    await logger.log_agent_header("b", "Node: B")

    await logger.log_agent_footer("b")
    step_a, step_b = fake_chainlit
    assert step_b.output == "✅ Completed"
    assert step_b.end is not None
    step_b.update.assert_awaited_once()
    step_a.update.assert_not_awaited()

    await logger.log_agent_footer("a", "❌ Failed")
    assert step_a.output == "❌ Failed"


@pytest.mark.asyncio
async def test_repeated_header_for_a_running_agent_is_a_noop(fake_chainlit):
    await logger.log_agent_header("a", "Node: A")
    await logger.log_agent_header("a", "Node: A")
    assert len(fake_chainlit) == 1


@pytest.mark.asyncio
async def test_agents_share_the_message_handler_step_as_parent_not_each_other(fake_chainlit):
    handler_step = MagicMock()
    handler_step.id = "handler-step-id"
    with patch.object(logger, "local_steps") as stack:
        stack.get.return_value = [handler_step]
        await logger.log_agent_header("a", "Node: A")
        await logger.log_agent_header("b", "Node: B")

    step_a, step_b = fake_chainlit
    assert step_a.parent_id == "handler-step-id"
    assert step_b.parent_id == "handler-step-id"


@pytest.mark.asyncio
async def test_a_summary_line_is_shown_under_its_agents_box(fake_chainlit):
    await logger.log_agent_header("market_data_agent", "Node: Market Data Agent")
    parent = fake_chainlit[0]
    parent.id = "parent-id"

    await logger.log_agent_content("market_data_agent", "🎯 Found 12 listings")

    child = fake_chainlit[1]
    assert child.name == "🎯 Found 12 listings"
    assert child.parent_id == "parent-id"


@pytest.mark.asyncio
async def test_a_line_arriving_after_its_box_finished_still_attaches_to_it(fake_chainlit):
    await logger.log_agent_header("market_data_agent", "Node: Market Data Agent")
    parent = fake_chainlit[0]
    parent.id = "parent-id"
    await logger.log_agent_footer("market_data_agent")

    await logger.log_agent_content("market_data_agent", "🔍 Late line")

    # No second box was created for the agent; only the child line.
    assert len(fake_chainlit) == 2
    assert fake_chainlit[1].parent_id == "parent-id"


@pytest.mark.asyncio
async def test_outside_the_chat_a_line_is_printed_and_relayed_for_the_running_request(capsys):
    with patch.object(logger, "_is_chainlit_active", return_value=False), \
         patch.object(logger, "publish_log_event_for_current_request", new_callable=AsyncMock) as relay:
        await logger.log_agent_content("zoning_law_agent", "✅ Done")

    assert "✅ Done" in capsys.readouterr().out
    relay.assert_awaited_once_with("zoning_law_agent", "✅ Done")
