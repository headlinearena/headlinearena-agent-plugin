# Migration Guide — `ha.py` → `ha_client` package

Status: **Phase 2 of the WorkBuddy Connector v3.0 baseline is landed**
(`docs/plans/workbuddy-connector-v3.md` §27–28, released as plugin v1.37.2–v1.37.5).
This guide documents what moved, what deliberately did not move, and what any
integration that touches `scripts/ha.py` internals needs to know.

## TL;DR for integrators

**Nothing is required of you.** The stable entrypoint is unchanged:

```bash
python3 scripts/ha.py <command> ...
```

Every skill in this repo, every documented quick-start command, and every
host (Claude Code, Codex CLI, Copilot CLI, npx, Hermes) keeps working without
modification. The refactor is internal: `ha.py` went from a large single file
to a thin, stable compositor over a small leaf package, `scripts/ha_client/`.

## What moved where

| `ha_client/` module | Contents (extracted in baseline §28 Steps 1–4) |
|---|---|
| `errors.py` | `HAFailure`, `fail()`, `note()` — verbatim; `fail()` still raises `HAFailure` (v1.18.0 behavior) |
| `prediction.py` | Sample parsing (`--samples`), per-shape forecast payload building, ternary `--probabilities` validation, legacy/civic body builders, the canonical uuid5 idempotency-key derivation |
| `contracts.py` | `prediction-contract-v2` response validation (fail-closed), v2-entry → Civic projections, legacy-macro and legacy-human-forecast projections, the `FORECAST_SUBMIT_HINTS` shape/hint table |
| `transport.py` | urllib send/decode/error-mapping core (`open_json`), request-header assembly, origin normalization + HTTPS enforcement, `expect` |
| `auth.py` | Token-freshness predicate (60 s refresh margin), token request/response shapes, claim/pairing message builders |
| `legacy.py` | Deprecated-macro compat routing: CN-endpoint guard, 403-scope / non-numeric-shape classifiers, missing-scope + redirect texts, the legacy 404-fallback body with its **own historical** uuid5 derivation |

Release mapping: v1.37.2 (`errors.py`, `prediction.py`) → v1.37.3 (`contracts.py`)
→ v1.37.4 (`transport.py`, `auth.py`) → v1.37.5 (`legacy.py`).

## What deliberately stayed in `ha.py`

The stateful orchestrators and everything tests intercept by name:

- `http()` / `authed()` / `get_token()` / `_absorb_status_headers()` — I/O compositors
- The credential store (`load_store` / `save_store` / `creds` / `update_creds`, `~/.headlinearena/credentials.json`)
- All `cmd_*` command entry points and argparse wiring
- The `_fetch_*` discovery fetchers and update-check machinery

Two reasons, both load-bearing:

1. **Patch-target contract.** The test suite intercepts I/O with
   `mock.patch.object(ha, "http")` / `ha.authed` / `ha.creds` / `ha.load_store`
   / `ha.update_creds` / `ha._fetch_*` / `ha._sync_claim_status`, which only
   works while the patched functions resolve their collaborators through
   ha's module globals *at call time*. Moving the orchestrators into the
   package would silently unpatch them (tests would pass mocks that real
   code never sees and start hitting the network).
2. **Host-adapter contract.** The Hermes adapter (`ha_tools.py`) does
   `import ha` and calls `cmd_*` directly, and catches `ha.HAFailure`.

## The alias re-import contract

`ha.py` re-imports every moved name under its **historical** name, e.g.
`build_forecast_payload as _build_forecast_payload`, so `ha._fetch_*`-style
patches, `except ha.HAFailure`, and any third-party `import ha` keep
resolving the exact same objects. This is pinned by tests
(`test_ha_client_package.py` asserts `ha.X is ha_client.<module>.x`).
**If you rename or move a symbol, keep the historical binding in `ha.py`'s
namespace** — treat it as public API.

## Rules for the `ha_client` package (leaf-only)

- `ha_client` modules must never import `ha` or call back into it.
- `ha_client` modules must not depend on each other's stateful layers;
  only pure helpers (`errors.fail`, leaf functions) may be shared.
- Everything in `ha_client` must be importable with stdlib only and without
  network/filesystem access at import time.

## The two uuid5 derivations (do not unify)

- Canonical (`prediction.build_civic_forecast_body`):
  `uuid5(NAMESPACE_URL, "{challenge_id}:{json.dumps(forecast, sort_keys=True)}:{amount}")`
- Legacy 404-fallback (`legacy.build_macro_civic_fallback_body`):
  `uuid5(NAMESPACE_URL, "{challenge_id}:{predicted_value}:{predicted_std}:{amount}")`

The server's idempotency dedup keys on the exact string. Both derivations are
pinned by hardcoded-digest regression tests and **must never be merged** —
unifying them would change dedup identity for historical submissions.

## For maintainers: extraction discipline (baseline §28)

Any future extraction must follow the same choreography used for Steps 1–4:

1. Move only pure/leaf code; keep orchestrators in `ha.py`.
2. Re-import under historical aliases; add an identity test.
3. Keep every error message byte-identical (tests pin the exact strings).
4. Run the full suite after each step; never batch multiple extractions.
5. Ship each step as its own patch release with a matching tag
   (see `CLAUDE.md` versioning rules and `scripts/check_version_sync.py`).

See also [compatibility.md](./compatibility.md) for the protected
user-facing surface, and the per-release notes in
[../CHANGELOG.md](../CHANGELOG.md).
