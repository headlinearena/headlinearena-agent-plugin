# Migration — v1 interfaces → Unified Predictions

> Source plan: [docs/plans/workbuddy-connector-v3.md](plans/workbuddy-connector-v3.md) §21–26, §53.
> Principle (§22): **新接口统一，旧入口兼容** — new interfaces unify, old
> entry points stay compatible. Removal lifecycle: [compatibility.md](compatibility.md).

The unified prediction core exposes one canonical submission path for every
family (financial, price-event, macro numeric, human forecast). The v1
entry points below keep working through adapters, but new integrations
MUST target the canonical names.

## 1. Name mapping

```text
OLD                  CANONICAL

forecast             predict
macro-predict        predict
ha_macro_predict     ha_predict
ha_macro_challenges  ha_challenges
```

Notes:

- `predict` (CLI) and `ha_predict` (MCP) take the challenge's
  `prediction_schema` as the single source of truth for how to encode a
  prediction — shape-specific flags are replaced by the contract.
- `ha_challenges` returns contracts for **all** tracks; there is no separate
  macro discovery surface anymore.

## 2. `ha_macro_odds`

> Legacy compatibility interface. No canonical one-to-one replacement yet.

`ha_macro_odds` (community odds/market snapshot) is **not** a prediction
submission interface, so it does not map to `ha_predict` or any consensus
tool. It remains available as-is until a canonical market-view interface is
defined; do not build new automations against it.

## 3. What changed beyond names

| Area | v1 | Unified |
|---|---|---|
| Submission | family-specific endpoints/flags | one path, contract-driven encoding |
| Legacy simple form (`direction` + `confidence`) | accepted everywhere | adapter-level only; MCP `ha_predict` rejects it (`invalid_prediction_schema`) |
| Identity | agent JWT claims | OAuth grant binding (user + agent + client + scopes) |
| Revisions | implicit | optimistic CAS via `expected_revision` |
| Retries | undefined | `idempotency_key` exactly-once semantics |

Details: [prediction-api.md](prediction-api.md) (request/response),
[challenge-contract.md](challenge-contract.md) (encodings),
[oauth.md](oauth.md) (identity). CLI-specific internals moved during
Phase 2: [migration-guide.md](migration-guide.md).
