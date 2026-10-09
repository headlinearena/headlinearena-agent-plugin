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
5. **ha_predict** with the challenge id + prediction.
6. Store the returned `revision_number` and `prediction_id`.
7. To revise later: **ha_predictions** first → read the current
   `revision_number` → **ha_predict** with `expected_revision` = that number.

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
