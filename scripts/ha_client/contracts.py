"""Prediction-contract discovery parsing and projections (Phase 2, Step 2).

Extracted from scripts/ha.py — see docs/plans/workbuddy-connector-v3.md
§27–28. Everything here is pure parsing/projection over already-fetched
contract data: no HTTP, no credential access. Unknown or malformed contract
responses fail closed through ha_client.errors.fail with the exact historical
message, so CLI behavior is unchanged. ha.py re-binds each moved function to
its old underscore name so existing callers and tests keep resolving.
"""

from ha_client.errors import fail

# Maps every outcome_shape the plugin can build a correct `forecast` payload
# for onto the CLI hint shown in discovery output. The key set doubles as the
# accepted outcome_shape enum when projecting contract entries — a shape not
# listed here cannot be submitted by this plugin version and is dropped from
# discovery rather than shown with a wrong hint.
FORECAST_SUBMIT_HINTS = {
    "numeric_distribution": "forecast <id> --mean <n> --std <n> --amount <n>  (or --samples <n,n,...>)",
    "binary_probability": "forecast <id> --yes-probability <0..1> --amount <n>",
    "ordered_categorical_distribution": "forecast <id> --probability CAT=P [--probability CAT=P ...] --amount <n>",
}


def forecast_submit_hint(item):
    """Schema-aware submit hint for one Civic discovery item.

    The shape-level table above is the default. One real exception: a
    numeric_distribution target whose frozen schema advertises
    input_encoding="empirical_samples" (bounded support) REJECTS
    --mean/--std server-side ("Frozen numeric support requires exactly
    samples"); its hint must lead with --samples or an agent following the
    hint 400s at submit time."""
    item = item if isinstance(item, dict) else {}
    hint = FORECAST_SUBMIT_HINTS.get(
        item.get("outcome_shape"), "forecast <id> --amount <n>"
    )
    schema = item.get("forecast_schema")
    if (
        item.get("outcome_shape") == "numeric_distribution"
        and isinstance(schema, dict)
        and schema.get("input_encoding") == "empirical_samples"
    ):
        hint = (
            "forecast <id> --samples @samples.json --amount <n>  "
            "(bounded support: raw samples required, --mean/--std rejected)"
        )
    return hint


def civic_asset_from_target_key(target_key):
    # "HF_US_CPI" -> "CPI"; drop the leading family tag and the region code.
    parts = (target_key or "").split("_")
    return "_".join(parts[2:]) if len(parts) > 2 else (target_key or "")


def parse_contract_response(resp):
    """Fail-closed validation of a prediction-contract-v2 response body.

    Returns the entries list. An unknown or malformed contract raises rather
    than degrading to the legacy endpoints — the caller may use those only
    when the discovery route itself is absent (HTTP 404), never to paper over
    contract drift."""
    if not isinstance(resp, dict) or resp.get("api_contract_version") != "prediction-contract-v2":
        fail(
            "Unsupported prediction discovery contract; expected prediction-contract-v2. "
            "Update the HeadlineArena plugin before submitting."
        )
    entries = resp.get("entries")
    if not isinstance(entries, list):
        fail("Malformed prediction-contract-v2 response: entries must be a list")
    return entries


def civic_from_contract_entry(entry):
    """Project one prediction-contract-v2 entry into the canonical Civic
    discovery item, or None when the entry is not a submittable open round."""
    if not isinstance(entry, dict):
        return None
    contract = entry.get("contract")
    challenge = entry.get("current_challenge")
    if not isinstance(contract, dict) or not isinstance(challenge, dict):
        return None
    execution_route = (contract.get("execution_family"), contract.get("submission_route"))
    if (
        contract.get("api_contract_version") != "prediction-contract-v2"
        or contract.get("site") != "global"
        or execution_route not in {
            ("human_forecast", "human_forecast"),
            ("macro_numeric", "macro_numeric_legacy"),
        }
        or contract.get("participation_contract") != "forecast_and_stake"
        or contract.get("submission_atomic") is not True
        or not {"prediction:submit", "credits:stake"}.issubset(
            set(contract.get("required_scopes") or [])
        )
        or contract.get("outcome_shape") not in FORECAST_SUBMIT_HINTS
        or not isinstance(contract.get("forecast_schema"), dict)
        or challenge.get("status") != "open"
    ):
        return None
    target_key = contract.get("target_key")
    return {
        "id": challenge.get("challenge_id"),
        "asset": civic_asset_from_target_key(target_key),
        "target_key": target_key,
        "scope_key": contract.get("scope_key", target_key),
        "region": contract.get("region"),
        "status": challenge.get("status"),
        "deadline": challenge.get("deadline"),
        "outcome_shape": contract.get("outcome_shape"),
        "forecast_schema": contract.get("forecast_schema"),
        "participation_contract": contract.get("participation_contract"),
        "required_scopes": contract.get("required_scopes"),
        "execution_family": contract.get("execution_family"),
        "submission_route": contract.get("submission_route"),
        "compatibility_status": contract.get("compatibility_status"),
    }


def legacy_macro_as_civic(item):
    """Project an already-open Legacy Macro round into the canonical Civic
    discovery shape. The route remains explicit so `forecast` preserves the
    round's frozen legacy write contract instead of pretending it was created
    by Human Forecast."""
    if not isinstance(item, dict) or not item.get("id"):
        return None
    canonical = item.get("canonical_target_key") or item.get("asset")
    return {
        "id": item.get("id"),
        "asset": civic_asset_from_target_key(canonical),
        "target_key": canonical,
        "scope_key": item.get("scope_key", canonical),
        "region": item.get("region"),
        "status": item.get("status", "open"),
        "deadline": item.get("deadline"),
        "unit": item.get("unit"),
        "outcome_shape": "numeric_distribution",
        "forecast_schema": {
            "outcome_shape": "numeric_distribution",
            "input_encoding": "normal_mean_std",
            "required_fields": ["mean", "std"],
            "additional_properties": False,
        },
        "participation_contract": "forecast_and_stake",
        "required_scopes": ["prediction:submit", "credits:stake"],
        "submission_route": "macro_numeric_legacy",
        "compatibility_status": item.get("compatibility_status", "legacy_open_round"),
    }


def civic_from_legacy_human_forecast(item):
    """Project an item from the deprecated /public/human-forecasts list (the
    pre-v2 rolling-deploy fallback) into the canonical Civic shape."""
    return {
        "id": item.get("id"),
        "asset": civic_asset_from_target_key(item.get("target_key", "")),
        "target_key": item.get("target_key"),
        "scope_key": item.get("scope_key", item.get("target_key")),
        "region": item.get("region"),
        "status": item.get("status"),
        "deadline": item.get("deadline"),
        "unit": item.get("unit"),
        "outcome_shape": item.get("outcome_shape"),
        "forecast_schema": item.get("forecast_schema"),
        "bins": item.get("bins"),
        "submission_route": "human_forecast",
    }
