"""Prediction validation and payload construction (Phase 2, Step 1).

Extracted verbatim from scripts/ha.py — see docs/plans/workbuddy-connector-v3.md
§27–28. Everything here is pure validation / body building: no HTTP, no
credential access, no printing. Invalid input raises HAFailure through
ha_client.errors.fail with the exact historical message, so CLI behavior is
unchanged. ha.py re-binds each moved function to its old underscore name
(``_build_forecast_payload`` etc.) so every existing caller, test, and
patch target keeps resolving.
"""

import json
import math
import re
import uuid

from ha_client.errors import fail


def parse_samples(raw):
    """Parse --samples: comma-separated numbers inline, or @path to a file
    containing a JSON array or newline/comma-separated numbers."""
    text = raw.strip()
    if text.startswith("@"):
        path = text[1:]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read().strip()
        except OSError as exc:
            fail(f"--samples file {path!r} could not be read: {exc}")
    if text.startswith("["):
        try:
            values = json.loads(text)
        except ValueError:
            fail("--samples JSON array could not be parsed")
        if not isinstance(values, list):
            fail("--samples JSON must be an array of numbers")
    else:
        values = [tok for tok in re.split(r"[,\s]+", text) if tok]
    parsed = []
    for item in values:
        try:
            parsed.append(float(item))
        except (TypeError, ValueError):
            fail(f"--samples entries must be numbers, got {item!r}")
    if not all(math.isfinite(v) for v in parsed):
        fail("--samples entries must all be finite")
    if not 10 <= len(parsed) <= 1000:
        fail(f"--samples needs between 10 and 1000 values, got {len(parsed)}")
    return parsed


def reject_client_bin(args):
    if getattr(args, "bin", None) is not None or getattr(args, "bin_label", None) is not None:
        fail(
            "The server maps your forecast statistic to exactly one frozen bin itself — "
            "clients cannot choose or split bins. Pass --mean/--std (or --samples), --yes-probability, "
            "or --probability instead of --bin/--bin-label."
        )


def build_forecast_payload(shape, args, challenge):
    schema = challenge.get("forecast_schema")
    if shape == "numeric_distribution":
        if getattr(args, "samples", None) is not None:
            if args.mean is not None or args.std is not None:
                fail("Pass either --samples or --mean/--std, not both")
            return {"samples": parse_samples(args.samples)}
        if args.mean is None or args.std is None:
            fail(
                f"This challenge is numeric_distribution — pass --mean and --std, "
                f"or --samples with your raw predictive samples (schema: {schema})"
            )
        if not math.isfinite(args.mean) or not math.isfinite(args.std):
            fail("--mean and --std must be finite numbers")
        if args.std <= 0:
            fail("--std must be > 0")
        return {"mean": args.mean, "std": args.std}
    if shape == "binary_probability":
        if args.yes_probability is None:
            fail(f"This challenge is binary_probability — pass --yes-probability (schema: {schema})")
        if not math.isfinite(args.yes_probability) or not 0.0 <= args.yes_probability <= 1.0:
            fail("--yes-probability must be between 0 and 1")
        return {"yes_probability": args.yes_probability}
    if shape == "ordered_categorical_distribution":
        categories = [
            item.get("key")
            for item in ((schema or {}).get("categories") or [])
            if isinstance(item, dict) and item.get("key")
        ]
        if not categories:
            categories = [b["category"] for b in (challenge.get("bins") or []) if "category" in b]
        if not args.probability:
            fail(
                f"This challenge is ordered_categorical_distribution — pass --probability "
                f"CAT=VALUE once per category {categories} (schema: {schema})"
            )
        probs = {}
        for item in args.probability:
            if "=" not in item:
                fail(f"--probability must be CATEGORY=VALUE, got {item!r}")
            cat, _, val = item.partition("=")
            cat = cat.strip()
            if not cat or cat in probs:
                fail(f"--probability categories must be non-empty and unique, got {cat!r}")
            try:
                probs[cat] = float(val)
            except ValueError:
                fail(f"--probability value must be a number, got {item!r}")
            if not math.isfinite(probs[cat]) or not 0.0 <= probs[cat] <= 1.0:
                fail(f"--probability values must be finite numbers between 0 and 1, got {item!r}")
        if categories and set(probs) != set(categories):
            fail(f"--probability categories {sorted(probs)} do not match the frozen set {categories}")
        tolerance = float((schema or {}).get("tolerance", 0.000001))
        if not math.isclose(sum(probs.values()), 1.0, rel_tol=0.0, abs_tol=tolerance):
            fail(
                f"--probability values must sum to 1 within tolerance {tolerance}; "
                f"got {sum(probs.values())}"
            )
        return {"probabilities": probs}
    fail(
        f"Unrecognized outcome_shape {shape!r} for this challenge — this plugin version may be "
        f"older than the backend's contract. Run `ha.py update-check`."
    )


def macro_predict_body(args):
    return {"predicted_value": args.predicted_value, "predicted_std": args.predicted_std,
            "amount": args.amount, **({"rationale": args.rationale} if args.rationale else {})}


def build_legacy_macro_body(forecast, amount, rationale=None):
    """Legacy Macro compatibility body for an already-open legacy round
    (numeric mean/std only, no idempotency key, no revision)."""
    body = {
        "predicted_value": forecast["mean"],
        "predicted_std": forecast["std"],
        "amount": amount,
    }
    if rationale:
        body["rationale"] = rationale
    return body


def build_civic_forecast_body(challenge_id, forecast, amount, idempotency_key=None,
                              rationale=None, expected_revision=None):
    """Canonical Human Forecast body. When no key is supplied, derives the
    stable uuid5 idempotency key from the exact submission content — same
    derivation the CLI has always used, kept byte-identical so server-side
    dedup behavior cannot drift."""
    body = {
        "forecast": forecast,
        "amount": amount,
        "idempotency_key": idempotency_key or uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{challenge_id}:{json.dumps(forecast, sort_keys=True)}:{amount}",
        ).hex,
    }
    if rationale:
        body["rationale"] = rationale
    if expected_revision is not None:
        body["expected_revision"] = expected_revision
    return body


def parse_probabilities_arg(raw):
    """Parse the --probabilities argument: a JSON object given as a string
    (CLI) or an already-decoded mapping (library callers). Returns it as-is
    when not a string."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            fail('--probabilities must be a JSON object, e.g. '
                 '\'{"bearish": 0.60, "neutral": 0.35, "bullish": 0.05}\'')
    return raw


def validate_ternary_vector(probabilities):
    """Validate a full bearish/neutral/bullish probability vector; returns it
    with every value coerced to float."""
    if not isinstance(probabilities, dict) or set(probabilities) != {"bearish", "neutral", "bullish"}:
        fail("probabilities must be an object with exactly the keys bearish, neutral, bullish")
    try:
        probabilities = {k: float(v) for k, v in probabilities.items()}
    except (TypeError, ValueError):
        fail("probabilities values must be numbers")
    if any(v < 0 for v in probabilities.values()) or abs(sum(probabilities.values()) - 1.0) > 1e-6:
        fail("probabilities must be non-negative and sum to 1 (tolerance 1e-6)")
    return probabilities
