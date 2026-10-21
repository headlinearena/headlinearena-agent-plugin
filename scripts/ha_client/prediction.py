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
            "amount": args.amount, **({"rationale": args.rationale} if args.rationale else {}),
            **model_attribution_fields(getattr(args, "model_provider", None),
                                       getattr(args, "model_name", None),
                                       getattr(args, "model_version", None))}


def build_legacy_macro_body(forecast, amount, rationale=None,
                            model_provider=None, model_name=None, model_version=None):
    """Legacy Macro compatibility body for an already-open legacy round
    (numeric mean/std only, no idempotency key, no revision)."""
    body = {
        "predicted_value": forecast["mean"],
        "predicted_std": forecast["std"],
        "amount": amount,
    }
    if rationale:
        body["rationale"] = rationale
    body.update(model_attribution_fields(model_provider, model_name, model_version))
    return body


def build_civic_forecast_body(challenge_id, forecast, amount, idempotency_key=None,
                              rationale=None, expected_revision=None,
                              model_provider=None, model_name=None, model_version=None):
    """Canonical Human Forecast body. When no key is supplied, derives the
    stable uuid5 idempotency key from the exact submission content — same
    derivation the CLI has always used, kept byte-identical so server-side
    dedup behavior cannot drift. Declared model fields are excluded from the
    key: they describe the submitter, not the forecast content."""
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
    body.update(model_attribution_fields(model_provider, model_name, model_version))
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


def parse_forecast_context(raw):
    """Optional bounded JSON metadata, inline or @file; never print rejected contents.

    The server decides validation, permissions and trust. This helper does not
    fetch references, infer a model, or invent run/observation metadata.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        if raw.startswith('@'):
            try:
                with open(raw[1:], 'rb') as handle:
                    raw = handle.read(32769)
                if len(raw) > 32768:
                    fail('forecast_context exceeds 32768 UTF-8 bytes')
                raw = raw.decode('utf-8')
            except (OSError, UnicodeError):
                fail('forecast_context file could not be read as bounded UTF-8 JSON')
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            fail('forecast_context must be a JSON object')
    if not isinstance(raw, dict):
        fail('forecast_context must be a JSON object')
    try:
        encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError):
        fail('forecast_context must contain finite JSON values')
    if len(encoded) > 32768:
        fail('forecast_context exceeds 32768 UTF-8 bytes')
    allowed = {'schema_version', 'decision', 'observed_at', 'run_ref', 'evidence_refs'}
    if set(raw) - allowed:
        fail('forecast_context contains unsupported fields; trust claims and payload bodies are forbidden')
    if raw.get('schema_version', '1') != '1' or raw.get('decision') not in (None, 'initial', 'update', 'hold', 'abstain'):
        fail('unsupported forecast_context schema_version or decision')
    refs = raw.get('evidence_refs')
    if isinstance(refs, list) and len(refs) > 32:
        fail('forecast_context exceeds 32 evidence references')
    return raw


MODEL_PROVIDER_MAX = 64
MODEL_NAME_MAX = 128
MODEL_VERSION_MAX = 64


def model_attribution_fields(provider, name, version):
    """Validate the optional per-prediction model triple and return its body keys.

    Mirrors the server gate (platform model_attribution.normalize_model_fields)
    so an invalid declaration fails on the client, before any request or stake
    side effect: provider+name are a pair (both or neither), version requires
    the pair, blank/whitespace-only or over-length values reject, case/path/
    suffix are preserved verbatim and no provider is ever guessed. Values are
    stripped; a blank version means "no version" and is omitted (never
    inherited). Returns {} when nothing is declared, else up to three keys.

    Only str values count as declared — argparse produces str|None, so any
    other type (e.g. a test Mock's auto-created attribute) reads as undeclared
    instead of exploding in len().
    """

    def _clean(value):
        return value.strip() if isinstance(value, str) else None

    p, n, ver = _clean(provider), _clean(name), _clean(version)
    if ver == '':
        ver = None
    if (p is None) != (n is None):
        fail('model_provider and model_name must be passed together '
             '(--model-provider with --model-name), or both omitted')
    if ver is not None and p is None:
        fail('model_version requires --model-provider and --model-name')
    for label, value, bound in (('model_provider', p, MODEL_PROVIDER_MAX),
                                ('model_name', n, MODEL_NAME_MAX),
                                ('model_version', ver, MODEL_VERSION_MAX)):
        if value is not None and not value:
            fail(f'{label} cannot be blank')
        if value is not None and len(value) > bound:
            fail(f'{label} must be at most {bound} characters')
    fields = {}
    if p is not None:
        fields = {'model_provider': p, 'model_name': n}
        if ver is not None:
            fields['model_version'] = ver
    return fields


def build_unified_predict_body(challenge_id, prediction=None, reasoning=None, summary=None,
                               amount=None, expected_revision=None, idempotency_key=None,
                               forecast_context=None, revision_reason=None, trigger_event_id=None,
                               model_provider=None, model_name=None, model_version=None):
    """SDK body builder for optional metadata and receipt-only reassessment.

    No request is sent here. Existing domain contracts remain server-authoritative.
    """
    context = parse_forecast_context(forecast_context)
    decision = (context or {}).get('decision')
    if prediction is None and decision != 'abstain':
        fail('prediction is required unless forecast_context.decision is abstain')
    if decision in ('hold', 'abstain'):
        if expected_revision is None or amount is not None:
            fail('hold/abstain requires expected_revision and cannot include amount')
    body = {'challenge_id': challenge_id}
    for key, value in (('prediction', prediction), ('reasoning', reasoning), ('summary', summary),
                       ('amount', amount), ('expected_revision', expected_revision),
                       ('idempotency_key', idempotency_key), ('forecast_context', context),
                       ('revision_reason', revision_reason), ('trigger_event_id', trigger_event_id)):
        if value is not None:
            body[key] = value
    body.update(model_attribution_fields(model_provider, model_name, model_version))
    return body
