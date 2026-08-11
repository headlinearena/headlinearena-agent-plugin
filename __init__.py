"""HeadlineArena Hermes plugin — entry point.

Registers every tool defined in ha_tools.py (named to avoid colliding with
Hermes's own `tools` package/`tools.registry` module). All the credential/
token/HTTP work is delegated to scripts/ha.py (the same zero-dependency CLI
the Claude Code/Codex/Copilot skills in this repo already use) — this file
and ha_tools.py are just the Hermes-shaped adapter around it.
"""
from __future__ import annotations

from .ha_tools import _TOOLS


def register(ctx) -> None:
    """Register all HeadlineArena tools. Called once by the plugin loader."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="headlinearena",
            schema=schema,
            handler=handler,
            emoji=emoji,
        )
    # Deliberately NOT calling ha.check_for_update() here: it only ever
    # note()s to stderr, which Hermes never captures — the message would be
    # silently lost while still consuming the once-a-day throttle, starving
    # ha_tools.py's _run() (which surfaces the same notice as a visible JSON
    # field on every tool call) for a full day. See ha_tools.py's _run().
