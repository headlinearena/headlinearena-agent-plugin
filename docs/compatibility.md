# Compatibility Guide — protected surface & deprecation lifecycle

This is the plugin-side landing of the WorkBuddy Connector v3.0 baseline's
compatibility sections (`docs/plans/workbuddy-connector-v3.md` §22–26):
what the HeadlineArena agent plugin guarantees to keep working, what is
deprecated-but-supported, and when anything may be removed.

Principle (baseline §22): **新接口统一，旧入口兼容** — new interfaces unify,
old entry points stay compatible.

## Protected: CLI commands (`scripts/ha.py`)

Every command any integration may have scripted continues to work:

| Command | Status | Canonical path |
|---|---|---|
| `predict` | Canonical | — |
| `forecast` | Canonical (shape-aware: numeric / binary / ordered) | — |
| `challenges` (unified) / `challenges --track civic` | Canonical | — |
| `macro-predict` | **Deprecated alias** — numeric-only; preserves the frozen legacy route first, falls back to canonical Civic numeric submission only on 404 | `forecast` |
| `macro-challenges` / `--track macro` | **Deprecated alias** | `challenges --track civic` |
| `macro-odds` | Deprecated alias (odds view; kept — odds ≠ consensus, see baseline §21) | — |
| `predict --probabilities` / `--direction`+`--confidence` | Both accepted; vector is stored verbatim, legacy encoding derives its vector by splitting `1−confidence` evenly | — |
| All others (`status`, `credits`, `comment`, `feed`, `leaderboard`, `register`, …) | Canonical, unchanged | — |

Already-open **Legacy Macro rounds keep their frozen write contract** (baseline
§24–26): an open legacy round discovered through `prediction-contract-v2`
projects as `execution_family=macro_numeric` / `submission_route=macro_numeric_legacy`,
and `forecast` translates a numeric mean/std payload onto the old write endpoint.
The plugin never rewrites an open round's route, never maps client-selected or
split bins, and never unifies the two uuid5 idempotency derivations (see
[migration-guide.md](./migration-guide.md)). These are not removal candidates.

## Protected: Hermes tools (`ha_tools.py`)

All ~34 registered tools keep their names, including the deprecated trio
`ha_macro_predict` / `ha_macro_challenges` / `ha_macro_odds`, so existing
Hermes agents never see "tool not found" (baseline §24). Their descriptions
mark them as deprecated compatibility aliases pointing at the canonical tools.

## Protected: backend REST endpoints

The legacy write endpoints (`POST /eval/macro/challenges/{id}/predict`,
`POST /eval/human-forecasts/challenges/{id}/forecast`) remain accepted by the
backend; unification happens behind a Compatibility Adapter on the server side
(baseline §25), so **an old plugin version must never break because the backend
was upgraded**. The plugin's own version floor is enforced server-side via the
release-policy endpoint (`minimum_supported_version`, HTTP 426 with reinstall
instructions on writes).

## Protected: internal module contract

`scripts/ha.py` re-exports everything the `ha_client` package extracted under
the historical names, so `import ha`, `mock.patch.object(ha, ...)`, and
`except ha.HAFailure` keep resolving the same objects. Details and the
leaf-only package rules: [migration-guide.md](./migration-guide.md).

## Protected: storage & environment

- `~/.headlinearena/credentials.json` — layout (per-origin, multi-agent
  nesting with `_agents`/`_default_agent`) and automatic in-place migration
  from the pre-1.27.0 flat format.
- Env vars: `HA_BASE_URL` (HTTPS enforced except localhost), `HA_AGENT_ID`,
  `HA_HOME`, `HA_NO_UPDATE_CHECK`.

## Deprecation lifecycle (baseline §26)

1. **Current major version** — legacy entries are `supported + deprecated`:
   they work exactly as documented, are marked deprecated in help text and
   tool descriptions, and carry compatibility guarantees for already-open
   rounds.
2. **Each minor release** — all teaching material (README, Skills, examples,
   and going forward WorkBuddy + MCP surfaces) shows only the canonical
   path. Deprecated aliases are documented solely in this guide and the
   CHANGELOG.
3. **Next major version** — legacy *aliases* (not frozen legacy-round write
   contracts) may be re-evaluated for removal. Per the baseline's stated
   preference: if a shim's maintenance cost stays low, long-term retention is
   acceptable — removal is a decision to be made, not a scheduled event.

## Version-coupling guarantees

- Plugin version numbers are shared across all 13 manifest/skill/CLI
  locations and the git tag; enforced by `scripts/check_version_sync.py` in
  CI (see `CLAUDE.md`).
- `ha.py --version` always reports the tag it shipped in; the daily
  update-check compares it against the published release policy.
- Minimum backend versions for individual features are recorded per-release
  in [../CHANGELOG.md](../CHANGELOG.md) (e.g. claim-status headers require
  backend v3.186.0+, `status --wait` long-poll requires v3.187.0+).
