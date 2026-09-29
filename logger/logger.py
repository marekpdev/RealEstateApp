import chainlit as cl
import sys
from chainlit.context import ChainlitContextException, local_steps
from chainlit.utils import utc_now

from events.publisher import publish_log_event_for_current_request

# Safe fallback dictionary to group console outputs beautifully when running via CLI
_cli_headers = {}


def _is_chainlit_active() -> bool:
    """Helper function to safely detect if a Chainlit UI session is live."""
    try:
        # If this succeeds without throwing an exception, we are safely inside the UI
        return cl.context.emitter is not None
    except ChainlitContextException:
        # If it throws, we are running a pure CLI terminal script
        return False


async def log_agent_header(key: str, text: str):
    if _is_chainlit_active():
        # Fetch or initialize the session-isolated step container dict
        active_steps = cl.user_session.get("active_agent_steps")
        if active_steps is None:
            active_steps = {}
            cl.user_session.set("active_agent_steps", active_steps)

        # Build and send the step drawer if it doesn't exist yet
        if key not in active_steps:
            # Chainlit wraps each incoming message's handler in its own step,
            # and everything shown for that message belongs under it (that is
            # what keeps these boxes above the final report). The parent is
            # looked up here rather than left for Chainlit to infer, since
            # inference only happens when a step is entered.
            open_steps = local_steps.get() or []
            handler_step_id = open_steps[-1].id if open_steps else None
            node_step = cl.Step(name=text, parent_id=handler_step_id, default_open=True)

            # Deliberately NOT entered as a context manager. Entering a Step
            # pushes it onto Chainlit's per-task stack of open steps, and
            # any step created while another is on that stack becomes its
            # child. Agents run in parallel, so several are open at once and
            # each would nest inside whichever opened before it. Sending the
            # step directly keeps every agent a top-level, independent step
            # and lets each be closed by its own footer, in any order. Every agent
            # shares the one handler step above as its parent, so they are
            # siblings of each other.
            node_step.start = utc_now()
            node_step.output = "⏳ Running..."
            await node_step.send()

            active_steps[key] = node_step
            cl.user_session.set("active_agent_steps", active_steps)
    else:
        # 💻 CLI Fallback: Track the name locally and print to console
        _cli_headers[key] = text
        sys.stdout.write(f"\n⚙️  [{text}]\n")
        sys.stdout.flush()


async def log_agent_content(parent_key: str, text: str):
    if _is_chainlit_active():
        active_steps = cl.user_session.get("active_agent_steps") or {}
        finished_steps = cl.user_session.get("finished_agent_steps") or {}

        # A line can arrive just after its agent's box was closed (lines are
        # relayed from another process); it still belongs under that box.
        parent_step = active_steps.get(parent_key) or finished_steps.get(parent_key)

        # Defensive check: if header wasn't initialized first, spawn it safely
        if parent_step is None:
            fallback_title = f"Node: {parent_key.replace('_', ' ').title()}"
            await log_agent_header(parent_key, fallback_title)
            active_steps = cl.user_session.get("active_agent_steps") or {}
            parent_step = active_steps[parent_key]
        child_step = cl.Step(name=text, parent_id=parent_step.id)

        # 🛠️ FORCE CHILD CONTEXT: Explicitly enter and exit the child step
        # so it renders as a completed milestone line instead of a spinning action item
        await child_step.__aenter__()
        await child_step.__aexit__(None, None, None)
    else:
        # 💻 CLI Fallback: Grab the corresponding header name to structure terminal logger
        header_title = _cli_headers.get(parent_key, parent_key.upper())
        sys.stdout.write(f"  └── [{header_title}]: {text}\n")
        sys.stdout.flush()

        # When this runs inside a graph run in the background worker there is
        # no chat session to draw into, so also relay the line, over the
        # job's live channel, to whoever is watching that job. A no-op
        # anywhere else (the command line, a test calling an agent directly).
        node_name = str(getattr(parent_key, "value", parent_key))
        await publish_log_event_for_current_request(node_name, text)


async def log_message(text: str):
    if _is_chainlit_active():
        await cl.Message(content=text).send()
    else:
        # 💻 CLI Fallback
        sys.stdout.write(f"\n💬 [Message]: {text}\n")
        sys.stdout.flush()


async def render_financial_report(text: str):
    """
    Renders the final financial report using high-fidelity Markdown in Chainlit
    or a structured format in the console.
    """
    if _is_chainlit_active():
        # Use cl.Message for high-fidelity markdown rendering.
        # This allows the report to use the full UI width and render HTML/Markdown correctly.
        await cl.Message(content=text).send()
    else:
        # 💻 CLI Fallback: Beautiful terminal rendering
        sys.stdout.write("\n" + "═" * 60 + "\n")
        sys.stdout.write(" 📊 FINAL REAL ESTATE INVESTMENT REPORT\n")
        sys.stdout.write("═" * 60 + "\n")
        sys.stdout.write(text + "\n")
        sys.stdout.write("═" * 60 + "\n\n")
        sys.stdout.flush()


async def log_agent_footer(key: str, output: str = "✅ Completed"):
    """
    Marks the agent's Step drawer as finished in Chainlit, replacing its
    "Running..." line with `output`. Ending it also stops its loading
    indicator and lets later messages appear after it.
    """
    if _is_chainlit_active():
        active_steps = cl.user_session.get("active_agent_steps") or {}
        if key in active_steps:
            step = active_steps[key]

            # Closed explicitly (not via __aexit__): the step was never
            # pushed onto Chainlit's open-step stack, see log_agent_header.
            step.end = utc_now()
            step.output = output
            await step.update()

            # Clean up the active reference, but remember the finished box so
            # a line that arrives late can still be shown under it.
            del active_steps[key]
            cl.user_session.set("active_agent_steps", active_steps)
            finished_steps = cl.user_session.get("finished_agent_steps") or {}
            finished_steps[key] = step
            cl.user_session.set("finished_agent_steps", finished_steps)
    else:
        # CLI Fallback: Optional separator
        sys.stdout.write(f"🏁 [{key.replace('_', ' ').upper()} COMPLETED]\n")
        sys.stdout.flush()