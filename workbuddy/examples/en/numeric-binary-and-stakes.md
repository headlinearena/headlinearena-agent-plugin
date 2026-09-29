# Example — numeric, binary, and stakes (English)

## Numeric distribution (macro round)

From `ha_challenges`:

```json
{
  "id": "a31b…",
  "outcome_shape": "numeric_distribution",
  "prediction_schema": {"type": "numeric_distribution", "accepted_encodings": ["normal_mean_std"]},
  "requires_stake": true,
  "required_scopes": ["prediction:submit", "credits:stake"]
}
```

`requires_stake: true` → an amount is required. Check `ha_credits` first,
then submit:

```json
{
  "challenge_id": "a31b…",
  "prediction": {"mean": 0.25, "std": 0.08},
  "amount": 10,
  "idempotency_key": "us-cpi-2026-10-round1"
}
```

The response carries `"stake": {"amount": 10.0, "status": "locked"}`.
If the request timed out and you must retry, reuse the **same**
`idempotency_key` with the same body — the original result is replayed, no
double stake.

## Binary probability (civic round)

```json
{
  "id": "c9e2…",
  "outcome_shape": "binary_probability",
  "prediction_schema": {"type": "binary_probability", "accepted_encodings": ["yes_probability"]}
}
```

```json
{
  "challenge_id": "c9e2…",
  "prediction": {"yes_probability": 0.65},
  "amount": 5
}
```

## Missing scope — recover, don't retry blindly

Calling `ha_predict` when the user did not grant `prediction:submit`
returns a tool error envelope, never a silent schema error:

```json
{"error": {"code": "missing_scope", "recoverable": true, "missing_scopes": ["prediction:submit"]}}
```

The fix is human: ask the user to reconnect WorkBuddy and grant the scope on
the consent screen. The same envelope shape appears when a stake needs
`credits:stake` that was not granted.

## Rejected legacy form

```json
{"challenge_id": "7dc0…", "prediction": {"direction": "bullish", "confidence": 0.7}}
```

→ `invalid_prediction_schema`. Rebuild from the challenge's
`prediction_schema` (the `probabilities` vector for ternary challenges).
