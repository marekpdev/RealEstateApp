# utils/logger.py
import chainlit as cl
import sys

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
            await node_step.send()
            active_steps[key] = node_step
    else:
        # 💻 CLI Fallback: Track the name locally and print to console
        _cli_headers[key] = text
        sys.stdout.write(f"\n⚙️  [{text}]\n")
        sys.stdout.flush()

async def log_agent_content(key: str, text: str):
    if _is_chainlit_active():
        active_steps = cl.user_session.get("active_agent_steps") or {}

        # Defensive check: if header wasn't initialized first, spawn it safely
        if key not in active_steps:
            fallback_title = f"Node: {key.replace('_', ' ').title()}"
            await log_agent_header(key, fallback_title)
            active_steps = cl.user_session.get("active_agent_steps")

        # Update the target UI step element dynamically
        ui_step = active_steps[key]
        ui_step.output = text
        await ui_step.update()
    else:
        # 💻 CLI Fallback: Grab the corresponding header name to structure terminal logs
        header_title = _cli_headers.get(key, key.upper())
        sys.stdout.write(f"  └── [{header_title}]: {text}\n")
        sys.stdout.flush()


async def log_message(text: str):
    if _is_chainlit_active():
        await cl.Message(content=text).send()
    else:
        # 💻 CLI Fallback
        sys.stdout.write(f"\n💬 [Message]: {text}\n")
        sys.stdout.flush()