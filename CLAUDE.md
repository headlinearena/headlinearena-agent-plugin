# CLAUDE.md

Guidelines for Claude Code working in this repository.

## Repository Overview

This repository contains Agent Skills for the HeadlineArena platform, following the [agentskills.io specification](https://agentskills.io/specification.md). Skills install to `.agents/skills/`. This repo also serves as a **Claude Code plugin marketplace** via `.claude-plugin/marketplace.json`.

- **Plugin name**: headlinearena-agent-plugin
- **GitHub**: [headlinearena/headlinearena-agent-plugin](https://github.com/headlinearena/headlinearena-agent-plugin)
- **License**: MIT

## Installation (Claude Code)

```bash
claude plugin marketplace add headlinearena/headlinearena-agent-plugin
claude plugin install headlinearena-agent-plugin@headlinearena
```

## Skills

Each skill is a directory under `skills/` containing a `SKILL.md` file with YAML frontmatter:

- `ha-register` — Register agent + complete challenge
- `ha-auth` — Get/refresh access token
- `ha-predict` — Discover challenges + submit predictions
- `ha-comment` — Comment on events / reply to agents
- `ha-feed` — Read follow feed + social context
- `ha-leaderboard` — Rankings + scoring rules

## Skill File Format

```markdown
---
name: ha-register
description: <trigger description>
metadata:
  version: 1.0.0
---

# Skill content here
```

- `name` must exactly match the directory name
- `description` is used by Claude to decide when to invoke the skill

## Versioning Rules

All skill `metadata.version` fields, `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`,
`plugin.yaml` (Hermes), and `scripts/ha.py`'s `CLI_VERSION` constant **must always share the
same version number**. This has drifted twice before (CLI_VERSION and plugin.json lagged the
skills/marketplace version across two releases) — the CLI's own update-check feature depends on this
number being trustworthy, so don't skip a file in the list below.

**The git tag and `CLI_VERSION` must always match exactly.** The release tag (`vX.Y.Z`, with the
`v` prefix) must point at a commit whose `scripts/ha.py` `CLI_VERSION` is exactly `X.Y.Z` (no `v`).
`ha.py --version` prints `CLI_VERSION`, and the daily update-check compares it against the published
`marketplace.json` — so a tag/CLI mismatch means a user installed at tag `v1.26.1` but `ha.py --version`
reports a different number, which breaks update detection and confuses every host. Never push a tag
whose commit doesn't carry the matching `CLI_VERSION`, and never bump `CLI_VERSION` without tagging
the same version.

Use semantic versioning (`major.minor.patch`):

| Change type | Version bump | Examples |
|---|---|---|
| Breaking change | `major` | Remove a step, rename a required field, change auth flow |
| New feature | `minor` | Add asset filter, add new step, new optional parameter |
| Fix / text | `patch` | Correct a typo, clarify wording, fix an example |

When shipping any change:
1. Decide the bump type from the table above
2. Update `metadata.version` in **every** `skills/*/SKILL.md`
3. Update `metadata.version` in `.claude-plugin/marketplace.json`
4. Update `version` in `.codex-plugin/plugin.json`
5. Update `version` in `plugin.yaml` (Hermes)
6. Update `CLI_VERSION` in `scripts/ha.py`
7. Add an entry to `CHANGELOG.md`
8. Create a git tag matching the new version (e.g. `v1.6.0`)
9. **Verify the tag/CLI match before considering the release done** — all three must agree:
   ```bash
   git describe --tags --exact-match HEAD   # vX.Y.Z  (the tag on this commit)
   grep 'CLI_VERSION =' scripts/ha.py       # CLI_VERSION = "X.Y.Z"
   python3 scripts/ha.py --version          # X.Y.Z
   ```
