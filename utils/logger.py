# utils/logger.py
import chainlit as cl
import sys
from datetime import datetime

from chainlit.context import ChainlitContextException

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
            node_step = cl.Step(name=text, default_open=True)

            # 🛠️ FORCE CONTEXT ENTRY: Register this step to Chainlit's active task stack
            await node_step.__aenter__()

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

        # Defensive check: if header wasn't initialized first, spawn it safely
        if parent_key not in active_steps:
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
        # 💻 CLI Fallback: Grab the corresponding header name to structure terminal logs
        header_title = _cli_headers.get(parent_key, parent_key.upper())
        sys.stdout.write(f"  └── [{header_title}]: {text}\n")
        sys.stdout.flush()


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


async def log_agent_footer(key: str):
    """
    Marks the agent's Step drawer as completed in Chainlit.
    This ensures that subsequent messages appear AFTER the step in the UI.
    """
    if _is_chainlit_active():
        active_steps = cl.user_session.get("active_agent_steps") or {}
        if key in active_steps:
            step = active_steps[key]

            # 🛠️ FORCE CONTEXT EXIT: Pop the step instance out of Chainlit's active run queue.
            # This handles timestamps, closes WebSocket allocations, and kills the loader UI element.
            await step.__aexit__(None, None, None)

            # Clean up the session reference
            del active_steps[key]
            cl.user_session.set("active_agent_steps", active_steps)
    else:
        # CLI Fallback: Optional separator
        sys.stdout.write(f"🏁 [{key.replace('_', ' ').upper()} COMPLETED]\n")
        sys.stdout.flush()