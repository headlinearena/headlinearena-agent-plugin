# Stake Policy

> Status: **Implemented** — backend `feat/unified-prediction-core`, §61–63 test
> suites green; production deployment pending. As-built contract; RFC 2119
> keywords retain their normative force.
> Source plan: [docs/plans/workbuddy-connector-v3.md](plans/workbuddy-connector-v3.md) §16–19.
> RFC 2119 keywords apply.

## 1. Why a new policy object

Staking is the only flow where a natural-language agent spends a human's
credits unattended. Guardrails MUST be enforced **server-side** — a rule
written only into a skill prompt ("don't stake more than 100") is not a
control.

The existing `wallet_policy` is **not** a stake policy and MUST NOT be
reused as one. Today it exposes exactly two caps:

| `wallet_policy` field | Actually caps |
|---|---|
| `max_balance` | total wallet holdings |
| `per_tx_limit` | a single owner → agent **top-up** |

As the current CLI's own help states: `per_tx_limit` is *"cap on a single
top-up — NOT a per-prediction spend cap; the platform has no separate
per-prediction credit limit today."* That gap is what `StakePolicy` closes.
`wallet_policy` remains untouched for top-ups.

## 2. `StakePolicy`

Per-agent, owned by the human account:

```json
{
  "max_stake_per_prediction": 100,
  "max_total_locked_stake": 500,
  "daily_stake_limit": 1000,
  "require_confirmation_above": 50
}
```

| Field | Semantics |
|---|---|
| `max_stake_per_prediction` | Upper bound on credits locked by a single prediction/revision. |
| `max_total_locked_stake` | Upper bound on the agent's total simultaneously **locked** stake. |
| `daily_stake_limit` | Upper bound on new/incremented stake within one calendar day. The day boundary is **fixed to UTC** — no user-local-timezone drift. Policy reads and `STAKE_POLICY_EXCEEDED` errors MUST carry `daily_window: {"timezone": "UTC", "reset_at": "<next UTC midnight, ISO8601>"}` so agents and users can reason about remaining allowance. |
| `require_confirmation_above` | Reserved for interactive confirmation UX (P1). In v1 it is stored and reported but does not gate MCP writes. |

Defaults:

- Agents created via the human-owned path (`POST /account/agents`,
  connector-hosted — the WorkBuddy case) MUST be created with a non-empty
  default `StakePolicy`; the numeric defaults are backend configuration,
  not contract.
- Agents with no policy record (legacy agents) are treated as unlimited —
  legacy parity — but any later policy write immediately enforces.

## 3. Enforcement point

`StakePolicy` is evaluated inside the write path, before any persisted
effect:

```text
ha_predict (MCP)  ─┐
ha.py predict (CLI)─┤
legacy REST        ─┼→ PredictionService.predict()
Hermes alias       ─┘        ↓ validate deadline → schema → scopes
                             ↓ StakePolicyService.evaluate()   ← here
                             ↓ idempotency → revision
                             ↓ atomic transaction → persist
```

Because the check lives in `PredictionService`, **no** transport (MCP tool,
skill, CLI flag, alias) can bypass it. Acceptance tests must attempt the
bypass and fail it (plan §67).

On violation:

```json
{
  "error": {
    "code": "STAKE_POLICY_EXCEEDED",
    "message": "Stake exceeds per-prediction cap.",
    "recoverable": true,
    "requested": 300,
    "max_stake_per_prediction": 100,
    "daily_window": { "timezone": "UTC", "reset_at": "2026-10-01T00:00:00Z" }
  }
}
```

`INSUFFICIENT_CREDITS` remains the distinct balance error.

## 4. Dual protection — scope consent + policy

Two independent gates, both required:

```text
gate 1  OAuth consent:  the human explicitly granted credits:stake
gate 2  StakePolicy:    the requested amount fits the caps
```

Consequences:

- WorkBuddy v1 MUST NOT expose `ha_scope`; an agent MUST NOT be able to
  self-grant `credits:stake` (`ha.py scope --add …` remains a first-party
  CLI affordance, not an MCP tool).
- Possessing the scope is necessary but not sufficient — every write still
  passes `StakePolicyService.evaluate()`.
- Revoking consent (re-scope via reconnect, or revoking the integration)
  removes gate 1 entirely.

## 5. Audit

Every stake transition (lock / release / settle) and every
`STAKE_POLICY_EXCEEDED` / `INSUFFICIENT_CREDITS` rejection MUST be written
to the append-only audit log with `timestamp / user_id / agent_id /
client_id / grant_id / request_id` (plan §45.1). Full tokens are never
logged.
