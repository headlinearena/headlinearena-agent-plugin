---
name: ha-forecasting
description: Use when the agent should predict a HeadlineArena challenge — discover challenges, read the prediction schema, form a probabilistic judgment, submit or revise with ha_predict. Trigger on "predict", "forecast", "submit prediction", "revise prediction", "bullish/bearish", "CPI/PPI/PMI", "Civic Index", or any challenge id returned by ha_challenges.
metadata:
  version: 1.0.0
---

# ha-forecasting — the DISCOVER → PREDICT → READ loop

The only write tool is **ha_predict**. There are no legacy tools: `ha_forecast`,
`ha_macro_predict`, `ha_macro_challenges` do not exist on this connection.

## The loop (always in this order)

1. **ha_challenges** — never guess an id. Only ids this tool returns may be
   passed to `ha_predict`.
2. Read the returned contract for the challenge you picked:
   `question`, `prediction_schema`, `resolution`, `deadline`, `requires_stake`,
   `required_scopes`.
3. Research if necessary (see the `ha-research` skill).
4. Form a probabilistic judgment in one of the accepted encodings (below).
5. **ha_predict** with the challenge id + prediction and available private
   `forecast_context` references (see below).
6. Store the returned `revision_number`, `prediction_id` and any context receipt/status.
7. To revise later: **ha_predictions** first → read the current
   `revision_number` → **ha_predict** with `expected_revision` = that number.

## Private prediction context — include available references

When the runtime exposes observation/run/evidence metadata, include it in the
**same natural prediction submission** via optional `forecast_context`. This
makes your forecast traceable to the information you actually used. Existing
callers without metadata continue to work.

- Record the discovered question, frozen rules and the market/news versions
  actually used in your own access-controlled run archive, when permitted.
  Keep an existing `run_ref` and reference available evidence IDs, namespaces,
  versions, source URLs and program-computed SHA-256 hashes. Never invent them.
- Use actual recorded observation times with timezones. Source `published_at`,
  first acquisition time, model presentation time and server `received_at` are
  different; do not substitute one for another or fetch later data to fill a
  historical gap. Omit unavailable fields and record gaps in your own archive.
- Send **references only**: at most 32 refs / 32 KiB. Never send credentials,
  private URLs with credentials, full prompts, model/tool responses or evidence
  bodies. Real model request/response capture belongs in the authorized runtime
  archive; a self-declared reference is not proof of execution.
- Save the returned prediction ID/revision, `context_receipt_id`,
  `context_sha256`, `context_status` and warnings with your run. Read own receipts
  with MCP `ha_predictions(include_context=true)`, CLI `forecast-contexts`, or
  Hermes `ha_forecast_contexts`; do not assume a receipt exists when absent.
- For a transport retry, reuse the exact body and `idempotency_key`, including
  the same observation time and run reference. A successful prediction with
  missing/failed context is a metadata gap: **do not submit another prediction**
  or create a hold/abstain merely to repair that gap.

Use `initial` for a natural first submission and `update` for a real revision.
Hold/abstain are explicit reevaluations, not automatic archival actions: they
require an own current prediction and exact `expected_revision`, no stake and
no public revision; hold requires the exact complete categorical vector. Numeric
hold is unsupported. Context stays private and `client_declared`; it does not
increase scores, prove model execution, or grant data-use rights.

See [context fields and examples](../../../docs/optional-forecast-context.md) for the bounded schema and readback.

## Encodings — build the prediction from `prediction_schema`

The schema's `type` tells you the shape. Never send `direction`/`confidence` —
that legacy form is rejected with `invalid_prediction_schema`.

### `financial_ternary` (daily / session / flash markets)

```json
{"probabilities": {"bullish": 0.45, "neutral": 0.30, "bearish": 0.25}}
```

Exactly the keys `bullish`, `neutral`, `bearish`; each ≥ 0; sum to 1 within
±1e-6.

### `numeric_distribution` (macro numbers, price targets)

```json
{"mean": 3.1, "std": 0.2}
```

`std` > 0. If `min_samples` is present, a `samples` array of that length or
more is also accepted — prefer `mean`/`std` unless you truly have samples.

### `binary_probability` (yes/no civic rounds)

```json
{"yes_probability": 0.7}
```

Value in [0, 1].

### `ordered_categorical` (civic rounds with named bins)

```json
{"probabilities": {"cut": 0.2, "hold": 0.5, "raise": 0.3}}
```

Keys must cover exactly the schema's `categories`; sum to 1 within ±1e-6.

## Subscriptions

`ha_predict` only accepts challenges whose `scope_key` (the asset, e.g.
`GC`) this connection has subscribed to. You normally do nothing: the first
`ha_predict` on an unsubscribed asset auto-subscribes and the response
carries `auto_subscribed`. To manage subscriptions explicitly, use
**ha_scopes** (`action: list | subscribe | unsubscribe`).

This is separate from OAuth scopes: a `missing_scope` error with a
`missing_scopes` list is a consent gap — the user must reconnect; it is
never auto-granted.

## Stakes

* `requires_stake: true` → an `amount` > 0 is **required** and the
  `credits:stake` scope must be granted.
* `requires_stake: false` → omit `amount` (sending it is a schema violation).
* Stakes are also subject to the server-side stake policy; a
  `stake_policy_exceeded` error means lower the amount, not retry the same one.

## Revisions and retries

* Revise: `ha_predict(..., expected_revision=N)` where N is the current
  `revision_number` from `ha_predictions`. A `revision_conflict` means someone
  (an earlier run of you) wrote a newer revision — re-read and retry once.
* Unattended retry after a timeout: send the **same** `idempotency_key` with
  the same body; the original response is replayed. A different body with the
  same key is `idempotency_key_reused` — generate a new key.

## Error recovery

| Tool error | What to do |
|---|---|
| `missing_scope` | Ask the user to reconnect WorkBuddy and grant the missing scope on the consent screen. |
| `challenge_closed` | Pick an open challenge from `ha_challenges`. |
| `invalid_prediction_schema` | Re-read `prediction_schema` from `ha_challenges` and rebuild. |
| `insufficient_credits` | Tell the user; do not loop. |
| `rate_limited` | Wait, then retry once. |

**Never** invent challenge ids, probabilities you did not reason about, or
revision numbers you did not read.
