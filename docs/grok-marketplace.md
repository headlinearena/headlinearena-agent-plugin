# Grok Build plugin marketplace — listing runbook

Submission-time reference for listing this plugin in the official xAI
plugin catalog: <https://github.com/xai-org/plugin-marketplace>. The catalog
is a GitHub-PR-reviewed index; nothing is vendored — our entry points at
this repo pinned to a full commit SHA.

Keep this file in sync with the plugin manifest in
`.grok-plugin/plugin.json` and the MCP surface in `docs/mcp-integration.md`.

---

## Plugin-side requirements (this repo)

- `.grok-plugin/plugin.json` — Grok Build manifest. Grok Build also accepts
  `.claude-plugin/plugin.json`, but this repo carries the Claude
  **marketplace** index (`.claude-plugin/marketplace.json`, a different
  file), so the explicit `.grok-plugin` manifest is required.
- `.mcp.json` — wires the hosted MCP server
  (`https://mcp.headlinearena.com/mcp`). Users authenticate via OAuth 2.1
  on first connect (PKCE, dynamic client registration — see
  `docs/oauth.md`); no credentials ship in the plugin.
- `assets/logo.svg` — listing icon (site mark).
- `README.md` + a stated license (MIT) — CONTRIBUTING requirements.

## Catalog entry to submit

Appended to the `plugins` array in `.grok-plugin/marketplace.json` **in the
xai-org/plugin-marketplace repo** (not this repo):

```json
{
  "name": "headlinearena",
  "description": "HeadlineArena forecasting: hosted MCP server plus skills. Discover prediction challenges on financial markets, macro data releases, and civic events, submit and revise probabilistic forecasts with reasoning, stake credits, and track scorecards and leaderboards.",
  "category": "finance",
  "source": {
    "source": "url",
    "url": "https://github.com/headlinearena/headlinearena-agent-plugin.git",
    "sha": "<full 40-char commit SHA of the release tag>"
  },
  "homepage": "https://headlinearena.com",
  "keywords": ["headlinearena", "forecasting", "prediction", "prediction market", "forecasting mcp"],
  "domains": ["headlinearena.com", "mcp.headlinearena.com"]
}
```

Field notes (README field table):

- `sha` MUST be the full 40-char lowercase commit SHA of the tagged
  release commit (tags/abbreviated SHAs are rejected by the validator).
  Find it: `git ls-remote https://github.com/headlinearena/headlinearena-agent-plugin.git <tag>`
  or `git rev-parse v1.39.0^{}` locally after pushing the tag.
- `category` is free-form ("e.g. development, deployment, monitoring");
  `finance` matches the ChatGPT-directory listing category. Existing
  catalog categories at time of writing: development, database,
  deployment, productivity, observability, monitoring — we are the first
  `finance`.
- `keywords` + `domains` power Grok Build's plugin CTA (the prompt that
  proactively suggests the plugin); keep them brand-scoped, not generic
  ("predict", "trading" alone would over-trigger).

## PR process (per CONTRIBUTING.md)

1. Fork `xai-org/plugin-marketplace`, clone the fork.
2. Add the entry above to `.grok-plugin/marketplace.json` (keep the array
   alphabetically / thematically consistent with neighbors — check how
   maintainers ordered it; remote entries live in the same array as
   first-party ones).
3. Regenerate the component index — never hand-edit it:

   ```bash
   python3 scripts/generate-plugin-index.py
   ```

4. Validate locally (CI runs the same):

   ```bash
   python3 scripts/validate-catalog.py
   ```

5. Open the PR. CI runs the validator; a code-owner review is required.
   Rejection reasons called out in CONTRIBUTING: unpinned/invalid SHA,
   stale generated index, prompt injection planted in `SKILL.md`, missing
   license.

## Updating the listing after approval

Ship the new plugin version first (version lockstep + git tag in this
repo, per `CLAUDE.md`), then open a catalog PR that bumps only the `sha`
to the new release commit (script exists upstream:
`scripts/bump-plugin-shas.py`). Tool/surface renames additionally need
the entry `description` refreshed.

## Relationship to the other listing targets

| Target | Manifest | Notes |
|---|---|---|
| Claude Code marketplace | `.claude-plugin/marketplace.json` | self-hosted index; update = push to main |
| ChatGPT plugin directory | `.codex-plugin/plugin.json` + `docs/chatgpt-directory-review-cases.md` | portal submission; domain verification via `OPENAI_APPS_CHALLENGE_TOKEN` |
| Grok Build marketplace | `.grok-plugin/plugin.json` + this doc | PR to `xai-org/plugin-marketplace`; update = bump `sha` |

The hosted MCP server (`mcp.headlinearena.com`) is the same surface behind
the ChatGPT listing; changes to tool names/scopes/endpoints must keep
`docs/mcp-integration.md` and both review docs in sync.
