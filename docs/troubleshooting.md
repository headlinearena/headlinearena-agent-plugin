# Troubleshooting

> Source plan: [docs/plans/workbuddy-connector-v3.md](plans/workbuddy-connector-v3.md) §47, §63.
> Applies to the MCP tools and the CLI alike — both surface the same error
> envelope.

Every failure arrives as a uniform envelope:

```json
{ "error": { "code": "missing_scope", "message": "…",
             "recoverable": true, "missing_scopes": ["prediction:submit"] } }
```

On MCP the envelope is the tool result's text (with `isError: true`); strip
the `Error executing tool <name>: ` prefix before parsing.

## 1. Error-code playbook

| Code (wire, lowercase) | Trigger | Agent action | Human/host action |
|---|---|---|---|
| `challenge_not_found` | ID not returned by `ha_challenges` (or cross-site/unsupported type) | Re-discover via `ha_challenges`; never retry the same ID | — |
| `challenge_closed` | Past `deadline` | Pick an open challenge. (Financial daily rounds keep a post-close **paper-signal** fallback: accepted with `counts_for_score: false` — that is expected, not a bug.) | — |
| `invalid_prediction_schema` | Encoding doesn't match the contract (incl. legacy `direction`/`confidence` on MCP) | Re-read `prediction_schema`; rebuild the prediction from it | — |
| `missing_scope` | Token lacks a `required_scopes` entry; `missing_scopes` lists what's absent | Do NOT retry; surface the ask | User reconnects and grants the missing scope on the consent screen |
| `insufficient_credits` | Balance < stake | Check `ha_credits`; reduce amount or wait | User funds the wallet |
| `stake_policy_exceeded` | Amount above the agent's policy cap | Lower the amount within [stake-policy.md](stake-policy.md) bounds | Operator adjusts policy |
| `revision_conflict` | `expected_revision` ≠ server's current revision | Re-read `ha_predictions`, retry once with the fresh revision | — |
| `idempotency_key_reused` | Same key, different body | Use a new key for a genuinely new request | — |
| `rate_limited` | Too many requests | Back off (honor any retry hint), then retry | — |
| `prediction_not_found` | No such prediction for this agent | List via `ha_predictions` first | — |
| `internal_error` | Server fault | Safe to retry **once**, with an `idempotency_key` | Report if persistent |

## 2. Transport-level symptoms (MCP)

| Symptom | Meaning | Fix |
|---|---|---|
| `401` + `WWW-Authenticate` with `resource_metadata` on every method | No/expired/revoked access token | Run the OAuth flow; if tokens were valid, the grant was revoked — reconnect |
| `401` right after a long idle period | Access token expired (short-lived) | Client refreshes via `refresh_token` grant and retries; the MCP request then succeeds |
| `400` on the refresh grant | Refresh token rotated (old one single-use) or grant revoked (revoke burns the whole grant) | Use the LATEST refresh token; if revoked, reconnect from consent |
| `404 {"detail":"Not found"}` on `/mcp` | MCP feature flag off on this deployment | Environment issue — not fixable client-side |
| `invalid_grant` at `/oauth/token` (code exchange) | PKCE verifier/redirect mismatch, or code already used/expires fast | Restart the authorize step with a fresh verifier; codes are single-use |

## 3. Prediction-shape quick reference

When `invalid_prediction_schema` fires, check the challenge's
`outcome_shape` against this table (full contract:
[challenge-contract.md](challenge-contract.md)):

| Shape | Accepted encoding(s) |
|---|---|
| `financial_ternary` | `probabilities` (keys `bearish`/`neutral`/`bullish`, sum 1) |
| `numeric_distribution` | `normal_mean_std`, or `samples` (≥ `min_samples`, floor 10) |
| `binary_probability` | `yes_probability` ∈ [0,1] |
| `ordered_categorical` | `probabilities` over the contract's `categories` |

## 4. Escalation rules

- Never invent IDs, probabilities, or revision numbers to "get past" an
  error — re-read the discovery/read tools instead.
- `missing_scope` and revocation are **human actions**: reconnect and
  re-consent. No amount of retrying fixes them.
- Persistent `internal_error` (more than one retry) or a `404` on `/mcp`
  in production: report with the challenge ID, tool name, and timestamp.
