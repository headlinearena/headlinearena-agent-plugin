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
    # The CLI's update-check nudge lives in main(), which this adapter bypasses
    # by calling cmd_* directly — so a Hermes host would otherwise never learn a
    # newer version shipped. Fire it once per session load to match the other
    # hosts (whose skills shell out to ha.py and already hit the check in
    # main()). `ha` is importable here because ha_tools put scripts/ on sys.path
    # at import time; check_for_update() is best-effort and never raises — the
    # guard is defense-in-depth since a crash here disables the whole plugin.
    try:
        import ha
        ha.check_for_update()
    except Exception:
        pass
