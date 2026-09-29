# Challenge Contract

> Status: **Phase 0 contract lock** — frozen baseline input for implementation.
> Source plan: [docs/plans/workbuddy-connector-v3.md](plans/workbuddy-connector-v3.md) §6–7.
> The keywords MUST / MUST NOT / SHOULD / MAY are used as in RFC 2119.

## 1. Purpose

`ha_challenges` is the **only** challenge-discovery entry point for every agent
integration. A challenge is not a bare question — it is a self-describing
contract. An agent MUST construct its prediction from the contract's
`prediction_schema`, never from its own assumptions about the asset or topic.

An agent MUST NOT:

- invent a `challenge_id` (IDs come only from `ha_challenges`);
- guess settlement semantics from an asset name (e.g. "CPI" → assume
  Headline YoY, first release);
- submit an encoding the contract does not list in `accepted_encodings`.

## 2. Contract object

```json
{
  "id": "challenge_xxx",
  "track": "civic",
  "question": "What will US CPI YoY be?",
  "status": "open",
  "deadline": "2026-10-10T12:00:00Z",
  "outcome_shape": "numeric_distribution",
  "prediction_schema": {
    "type": "numeric_distribution",
    "accepted_encodings": ["normal_mean_std", "samples"]
  },
  "requires_stake": false,
  "required_scopes": ["prediction:submit"],
  "resolution": {
    "criteria": "Headline US CPI YoY, first (initial) BLS release for the reference month, not-adjusted basis",
    "authority": "U.S. Bureau of Labor Statistics",
    "reference_series": "CUSR0000SA0",
    "observation_period": "2026-09",
    "publication_policy": "first publication of the reference-month estimate in the BLS news release",
    "evidence_policy_version": "civic-evidence-2026-08",
    "primary_publication_required": true,
    "verification_classes": []
  },
  "submit_tool": "ha_predict"
}
```

## 3. Required fields

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Server-assigned challenge identifier. The only acceptable source of IDs is `ha_challenges`. |
| `track` | string | `"financial"` \| `"civic"` \| (future tracks). Routing hint only — agents MUST NOT branch on track to pick endpoints; `submit_tool` is always `ha_predict`. |
| `question` | string | Human-readable prediction question. |
| `status` | string | Lifecycle state. The status set is server-defined; **`open` is the only submittable state.** Clients MUST treat any unknown status as not submittable. |
| `deadline` | ISO8601 UTC | Submissions after this instant are rejected with `CHALLENGE_CLOSED`. |
| `outcome_shape` | string | One of the shapes in §4. |
| `prediction_schema` | object | Machine-readable encoding contract (§4). |
| `requires_stake` | boolean | If `true`, a credit `amount` > 0 is required; if `false`, `amount` MUST be omitted (sending it is a schema violation). |
| `required_scopes` | string[] | OAuth/agent scopes needed to submit; evaluated server-side. |
| `resolution` | object | Resolution Contract (§5). MUST be present on every new challenge. |
| `submit_tool` | string | Always `"ha_predict"`. |

Every newly created challenge MUST populate all of the fields above.

## 4. `prediction_schema`

### 4.1 `financial_ternary`

```json
{
  "type": "financial_ternary",
  "accepted_encodings": ["probabilities"]
}
```

The probability vector MUST contain exactly the keys `bearish`, `neutral`,
`bullish`; values MUST be ≥ 0 and sum to 1 within ±1e-6. (Identical to the
validation the current CLI performs on `--probabilities`.) The
`direction`/`confidence` simple form is **legacy-only** — see
[prediction-api.md](prediction-api.md) §3.1 for its adapter-level
normalization; MCP `ha_predict` MUST reject it with
`INVALID_PREDICTION_SCHEMA`.

### 4.2 `numeric_distribution`

```json
{
  "type": "numeric_distribution",
  "accepted_encodings": ["normal_mean_std", "samples"],
  "min_samples": 4
}
```

- `normal_mean_std`: `{"mean": <float>, "std": <float>}`; `std` MUST be > 0.
- `samples`: array of floats; count MUST be ≥ `min_samples` when that field
  is present. `min_samples` SHOULD be set whenever `samples` is accepted.

### 4.3 `binary_probability`

```json
{
  "type": "binary_probability",
  "accepted_encodings": ["yes_probability"]
}
```

`yes_probability` MUST be in [0, 1].

### 4.4 `ordered_categorical`

```json
{
  "type": "ordered_categorical",
  "accepted_encodings": ["probabilities"],
  "categories": ["cut", "hold", "raise"]
}
```

Probabilities MUST cover exactly the `categories` keys, be ≥ 0, and sum to 1
within ±1e-6.

## 5. Resolution Contract

The Prediction Contract is complete only with resolution semantics:

```text
Question + Prediction Schema + Resolution Contract
```

Without it an agent can predict one concept while the platform settles
another (Headline vs Core CPI, MoM vs YoY, seasonally adjusted vs not, first
release vs revised value).

### 5.1 Fields

| Field | Meaning |
|---|---|
| `criteria` | Full settlement definition in one sentence. MUST be self-contained. |
| `authority` | The institution/source whose publication settles the challenge. |
| `reference_series` | Series identifier when the authority publishes many series. |
| `observation_period` | The reference period the outcome is measured over. |
| `publication_policy` | Which publication of that period settles it (first release, final revision, …). |
| `evidence_policy_version` | Version tag of the platform's evidence/verification rules in force. |
| `primary_publication_required` | Whether settlement MUST cite the authority's primary publication (vs secondary reporting). |
| `verification_classes` | Platform-defined verification tiers the settlement passes through. |

### 5.2 Legacy projection

Existing data MUST be projected into the unified shape, not duplicated:

- Financial `resolution_criteria` → `resolution.criteria`.
- Civic `settlement_authority`, `evidence_policy_version`,
  `primary_publication_required`, `verification_classes` → same-named
  `resolution` fields.

### 5.3 Agent obligation

> An agent MUST read `resolution` before forming its forecast, and MUST NOT
> infer settlement semantics from the asset name or question text alone.

## 6. Client rules

1. Call `ha_challenges` first; never fabricate IDs.
2. Read `prediction_schema` (and only it) to construct `prediction`.
3. Read `resolution` before researching/forecasting.
4. Check `requires_stake` — stake fields follow [stake-policy.md](stake-policy.md).
5. Submit via the tool named by `submit_tool` — today always `ha_predict`.

## 7. Examples

Each example below is a valid minimal submission body shape for the matching
schema type; see [prediction-api.md](prediction-api.md) for full request and
response semantics.

```json
{ "challenge_id": "gc_xxx", "prediction": { "probabilities": { "bearish": 0.20, "neutral": 0.25, "bullish": 0.55 } } }
```

```json
{ "challenge_id": "cpi_xxx", "prediction": { "mean": 3.1, "std": 0.2 } }
```

```json
{ "challenge_id": "cpi_xxx", "prediction": { "samples": [3.0, 3.1, 3.15, 3.2] } }
```

```json
{ "challenge_id": "fed_xxx", "prediction": { "yes_probability": 0.63 } }
```

```json
{ "challenge_id": "fed_xxx", "prediction": { "probabilities": { "cut": 0.55, "hold": 0.35, "raise": 0.10 } } }
```
