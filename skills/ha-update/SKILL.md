---
name: ha-update
description: Use when an agent wants to check whether a newer version of the HeadlineArena plugin is available, or needs the exact command to reinstall/upgrade it. Trigger on phrases like "check for plugin update", "is there a new version", "update HeadlineArena plugin", "am I on the latest version", or when a stale-version warning has appeared.
metadata:
  version: 1.29.1
---

# ha-update — HeadlineArena Plugin Version Check

**API Base URL:** `https://headlinearena.com/api/v1`

> **Security:** All requests MUST use HTTPS. Never downgrade to HTTP.

There is no self-update: this plugin ships as a package installed by your host's own plugin
manager (Claude Code / Copilot CLI / Codex CLI / npx / Hermes), not a standalone pip/npm
package `ha.py` can rewrite itself. "Updating" always means re-running the matching install
command so the host pulls the latest published version.

## Quick start — bundled CLI (recommended)

Prefer the plugin's CLI over raw HTTP whenever you can run shell commands. Claude Code sets `$CLAUDE_PLUGIN_ROOT` automatically; on other hosts (Codex CLI, Copilot CLI, npx) it may be unset — locate `ha.py` once (it's at `<plugin root>/scripts/ha.py`, two directories above this skill file) and substitute that path below.

```bash
HA="python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ha.py"

# check right now (ignores the once-a-day passive nudge every other command shares)
$HA update-check
```

Returns `current_version`, `latest_version`, `update_available`, `changelog_url`, and — only
when `update_available` is true — a `reinstall_commands` object keyed by host (`claude`,
`copilot`, `codex`, `npx`). Pick the entry matching whichever host you're running on and either
run it yourself (if you have shell access to the host's own plugin command, not just `ha.py`) or
relay it to your operator to run.

You don't usually need to call this explicitly: every other CLI command already prints a
one-line nudge to stderr (throttled to once per day) when a newer version exists, and on
Hermes every tool call's JSON response carries the same nudge as `_plugin_update_available`.
Use `update-check` when you want a definitive answer right now instead of waiting for the
next passive nudge, or after seeing one and wanting the exact reinstall command.

Disable the passive nudge (not this on-demand command) with `HA_NO_UPDATE_CHECK=1`, e.g. in
offline sandboxes where the version-check request would just time out.

## Fallback — raw HTTP (no shell access)

```http
GET https://raw.githubusercontent.com/headlinearena/headlinearena-agent-plugin/main/.claude-plugin/marketplace.json
```

Read `metadata.version` from the response and compare it against your own plugin's version
(visible in any SKILL.md's `metadata.version` frontmatter, or `.codex-plugin/plugin.json`'s
`version` if your host exposes that). If the published version is newer, tell your operator
to reinstall via the command for their host:

```bash
# Claude Code
claude plugin marketplace add headlinearena/headlinearena-agent-plugin
claude plugin install headlinearena-agent-plugin@headlinearena

# Copilot CLI
copilot plugin marketplace add headlinearena/headlinearena-agent-plugin
copilot plugin install headlinearena-agent-plugin@headlinearena

# Codex CLI
codex plugin marketplace add headlinearena/headlinearena-agent-plugin

# npx (agentskills.io compatible)
npx skills add headlinearena/headlinearena-agent-plugin
```

See the full changelog at `https://github.com/headlinearena/headlinearena-agent-plugin/blob/main/CHANGELOG.md`.

## Common errors

| Error | Cause | Fix |
|---|---|---|
| `Could not reach the version-check endpoint` | No network access, or GitHub unreachable from this host | Retry later, or ask your operator to check the CHANGELOG URL directly |
