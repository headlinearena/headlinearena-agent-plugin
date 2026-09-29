"""Legacy Macro compatibility layer (Phase 2, Step 4).

The deprecated-compat routing decisions extracted from scripts/ha.py — see
docs/plans/workbuddy-connector-v3.md §27–28 and the convergence policy in
§24–26: every already-open legacy round keeps its frozen write contract, so
nothing here is removal candidates — this module is where those scattered
compat decisions live so the canonical paths stay clean. Command entry
points stay in ha.py (tests call them with ha.authed/ha.http/ha.out
patched); these are the pure predicates, message builders, and the legacy
404-fallback body with its own historical uuid5 derivation (distinct from
build_civic_forecast_body's — the server dedups on the exact string, so the
two derivations must never be unified).
"""

import re
import uuid

NON_NUMERIC_SHAPE_MESSAGE = (
    "This challenge is not numeric — macro-predict only supports "
    "outcome_shape=numeric_distribution (mean/std). Use "
    "`ha.py forecast <id> ...` instead (run `ha.py challenges --track civic` "
    "to see the exact flags for this challenge_id)."
)


def is_cn_endpoint(origin_str):
    """True if the base URL points at the CN regional deployment — a host
    ending in .cn (e.g. headlinearena.cn) or a /cn/ path segment in the base
    (the old /api/v1/cn/... form). The CN region is discontinued; callers
    refuse it so an agent never silently lands on a dead deployment."""
    o = (origin_str or "").lower()
    host = o.split("://", 1)[-1].split("/", 1)[0]
    norm = o if o.endswith("/") else o + "/"  # catch a trailing /cn (no slash)
    return host.endswith(".cn") or "/cn/" in norm


def is_missing_scope(status, resp):
    """The backend signals a missing OAuth scope as a 403 whose detail
    mentions "scope" (case-insensitive)."""
    return status == 403 and "scope" in str(resp.get("detail", "")).lower()


def is_non_numeric_shape_error(resp):
    """A 400-validation detail proving the challenge wants a forecast shape
    macro-predict cannot construct (binary/categorical) — the trigger for the
    redirect-to-forecast message."""
    detail = str(resp.get("detail", "")).lower()
    return "yes_probability" in detail or "probabilities" in detail


def missing_scope_message(command):
    """The self-grant hint, byte-identical to the historical per-command
    inline texts (macro-predict / forecast)."""
    return (
        f"Missing a required scope — {command} needs credits:stake, which is NOT granted "
        f"by default. Self-grant with: `ha.py scope --add credits:stake`, then re-run."
    )


def quoted_scope_key(detail):
    """The first single-quoted token in a 403 detail — the backend names the
    missing prediction-scope key that way (e.g. Not subscribed to 'GC')."""
    match = re.search(r"'([A-Za-z0-9_]+)'", detail)
    return match.group(1) if match else None


def build_macro_civic_fallback_body(challenge_id, predicted_value, predicted_std,
                                    amount, rationale=None):
    """The Human-Forecast retry body macro-predict uses when the legacy route
    404s. Derivation is the historical one — value:std:amount, NOT the
    canonical forecast-JSON form — and must stay that way: the server's
    idempotency dedup keys on it."""
    body = {
        "forecast": {"mean": predicted_value, "std": predicted_std},
        "amount": amount,
        "idempotency_key": uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{challenge_id}:{predicted_value}:{predicted_std}:{amount}",
        ).hex,
    }
    if rationale:
        body["rationale"] = rationale
    return body
