#!/usr/bin/env python3
"""Create a local, auditable forecasting-agent scaffold from validated JSON.

This script is intentionally offline and stdlib-only. It does not register,
deploy, schedule, fund, or run an agent, and it never calls HeadlineArena APIs.
"""

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path


SCHEMA_VERSION = 1
BUILDER_VERSION = "1.35.0"
MAX_SPEC_BYTES = 1024 * 1024
TOP_LEVEL_FIELDS = {
    "schema_version",
    "name",
    "setup_mode",
    "objective",
    "forecasting_for_good",
    "targets",
    "horizon",
    "outcome",
    "data",
    "schedule",
    "evaluation",
    "operation",
    "extensions",
}
REQUIRED_FIELDS = TOP_LEVEL_FIELDS - {"operation", "extensions"}
OUTCOME_TYPES = {"ternary", "binary", "numeric", "ordered"}
SUBMISSION_POLICIES = {"draft_only", "human_review", "autonomous_after_deploy"}


class SpecError(ValueError):
    """Raised when the requested scaffold is unsafe or invalid."""


def _text(value, path, *, max_length=2000):
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{path} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise SpecError(f"{path} must be at most {max_length} characters")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise SpecError(f"{path} contains unsupported control characters")
    return value


def _string_list(value, path, *, min_items=0, exact_items=None):
    if not isinstance(value, list):
        raise SpecError(f"{path} must be an array")
    if exact_items is not None and len(value) != exact_items:
        raise SpecError(f"{path} must contain exactly {exact_items} items")
    if len(value) < min_items:
        raise SpecError(f"{path} must contain at least {min_items} items")
    items = [
        _text(item, f"{path}[{index}]", max_length=300)
        for index, item in enumerate(value)
    ]
    if len({item.casefold() for item in items}) != len(items):
        raise SpecError(f"{path} must not contain duplicate values")
    return items


def _mapping(value, path):
    if not isinstance(value, dict):
        raise SpecError(f"{path} must be an object")
    return value


def _known_fields(value, path, allowed):
    obj = _mapping(value, path)
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise SpecError(
            f"{path} has unknown field(s): {', '.join(unknown)}; "
            "put custom settings under extensions"
        )
    return obj


def _required_text_fields(value, path, fields):
    obj = _known_fields(value, path, fields)
    missing = sorted(set(fields) - set(obj))
    if missing:
        raise SpecError(f"{path} is missing required field(s): {', '.join(missing)}")
    return {field: _text(obj[field], f"{path}.{field}") for field in fields}


def _validate_outcome(value):
    outcome = _mapping(value, "outcome")
    outcome_type = _text(outcome.get("type"), "outcome.type", max_length=20)
    if outcome_type not in OUTCOME_TYPES:
        raise SpecError("outcome.type must be ternary, binary, numeric, or ordered")

    if outcome_type == "ternary":
        _known_fields(outcome, "outcome", {"type", "labels"})
        labels = _string_list(
            outcome.get("labels"), "outcome.labels", exact_items=3
        )
        return {"type": outcome_type, "labels": labels}
    if outcome_type == "binary":
        _known_fields(outcome, "outcome", {"type", "labels"})
        labels = _string_list(
            outcome.get("labels"), "outcome.labels", exact_items=2
        )
        return {"type": outcome_type, "labels": labels}
    if outcome_type == "ordered":
        _known_fields(outcome, "outcome", {"type", "labels"})
        labels = _string_list(
            outcome.get("labels"), "outcome.labels", min_items=2
        )
        return {"type": outcome_type, "labels": labels}

    _known_fields(outcome, "outcome", {"type", "unit", "encoding"})
    unit = _text(outcome.get("unit"), "outcome.unit", max_length=100)
    encoding = _text(outcome.get("encoding"), "outcome.encoding", max_length=20)
    if encoding not in {"normal", "samples"}:
        raise SpecError("outcome.encoding must be normal or samples for a numeric outcome")
    return {"type": outcome_type, "unit": unit, "encoding": encoding}


def validate_spec(raw):
    spec = _mapping(raw, "spec")
    unknown = sorted(set(spec) - TOP_LEVEL_FIELDS)
    missing = sorted(REQUIRED_FIELDS - set(spec))
    if unknown:
        raise SpecError(
            f"unknown top-level field(s): {', '.join(unknown)}; "
            "put custom settings under extensions"
        )
    if missing:
        raise SpecError(f"missing required field(s): {', '.join(missing)}")
    if spec["schema_version"] != SCHEMA_VERSION:
        raise SpecError(f"schema_version must be the integer {SCHEMA_VERSION}")

    name = _text(spec["name"], "name", max_length=80)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]*", name):
        raise SpecError(
            "name may contain letters, numbers, spaces, dots, underscores, and hyphens"
        )
    _slug(name)
    setup_mode = _text(spec["setup_mode"], "setup_mode", max_length=20)
    if setup_mode not in {"guided", "custom"}:
        raise SpecError("setup_mode must be guided or custom")

    objective = _required_text_fields(
        spec["objective"], "objective", ("statement", "beneficiaries", "decision_use")
    )
    good = _known_fields(
        spec["forecasting_for_good"], "forecasting_for_good", {"eligible", "rationale"}
    )
    if good.get("eligible") is not True:
        raise SpecError("forecasting_for_good.eligible must be true before scaffolding")
    forecasting_for_good = {
        "eligible": True,
        "rationale": _text(good.get("rationale"), "forecasting_for_good.rationale"),
    }

    data = _known_fields(
        spec["data"], "data", {"allowed_sources", "excluded_sources", "freshness"}
    )
    normalized_data = {
        "allowed_sources": _string_list(
            data.get("allowed_sources"), "data.allowed_sources", min_items=1
        ),
        "excluded_sources": _string_list(
            data.get("excluded_sources", []), "data.excluded_sources"
        ),
        "freshness": _text(data.get("freshness"), "data.freshness"),
    }
    schedule = _known_fields(spec["schedule"], "schedule", {"cadence", "triggers"})
    normalized_schedule = {
        "cadence": _text(schedule.get("cadence"), "schedule.cadence"),
        "triggers": _string_list(schedule.get("triggers", []), "schedule.triggers"),
    }
    evaluation = _known_fields(
        spec["evaluation"],
        "evaluation",
        {"primary_metric", "review_cadence", "minimum_resolved_forecasts"},
    )
    minimum = evaluation.get("minimum_resolved_forecasts")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise SpecError("evaluation.minimum_resolved_forecasts must be a positive integer")
    normalized_evaluation = {
        "primary_metric": _text(
            evaluation.get("primary_metric"),
            "evaluation.primary_metric",
            max_length=100,
        ),
        "review_cadence": _text(
            evaluation.get("review_cadence"), "evaluation.review_cadence"
        ),
        "minimum_resolved_forecasts": minimum,
    }

    operation = _known_fields(
        spec.get("operation", {}),
        "operation",
        {"submission_policy", "max_credits_per_run"},
    )
    submission_policy = operation.get("submission_policy", "draft_only")
    if submission_policy not in SUBMISSION_POLICIES:
        raise SpecError("operation.submission_policy is invalid")
    max_credits = operation.get("max_credits_per_run", 0)
    if (
        isinstance(max_credits, bool)
        or not isinstance(max_credits, (int, float))
        or not math.isfinite(max_credits)
        or max_credits < 0
    ):
        raise SpecError("operation.max_credits_per_run must be zero or a positive number")
    extensions = _mapping(spec.get("extensions", {}), "extensions")

    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "setup_mode": setup_mode,
        "objective": objective,
        "forecasting_for_good": forecasting_for_good,
        "targets": _string_list(spec["targets"], "targets", min_items=1),
        "horizon": _text(spec["horizon"], "horizon"),
        "outcome": _validate_outcome(spec["outcome"]),
        "data": normalized_data,
        "schedule": normalized_schedule,
        "evaluation": normalized_evaluation,
        "operation": {
            "submission_policy": submission_policy,
            "max_credits_per_run": max_credits,
        },
        "extensions": extensions,
    }


def _slug(name):
    value = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    if not value:
        raise SpecError("name does not produce a usable directory slug")
    return value[:64].rstrip("-")


def _inside_workspace(workspace, relative_output):
    output_arg = Path(relative_output)
    if output_arg.is_absolute() or ".." in output_arg.parts:
        raise SpecError("--output must be a relative path without '..'")
    workspace = workspace.resolve()
    output = (workspace / output_arg).resolve()
    if output == workspace:
        raise SpecError("--output must not be the workspace root")
    try:
        output.relative_to(workspace)
    except ValueError:
        raise SpecError("--output resolves outside --workspace")
    return output


def _one_line(value):
    return " ".join(value.split())


def _bullets(values):
    return "\n".join(f"- {_one_line(value)}" for value in values) or "- None specified"


def _reject_json_constant(value):
    raise SpecError(f"spec contains non-finite JSON number: {value}")


def render_instructions(spec):
    outcome = spec["outcome"]
    if outcome["type"] == "numeric":
        outcome_text = f"numeric ({outcome['unit']}; {outcome['encoding']} encoding)"
    else:
        outcome_text = f"{outcome['type']}: {', '.join(outcome['labels'])}"
    triggers = _bullets(spec["schedule"]["triggers"])
    excluded = _bullets(spec["data"]["excluded_sources"])
    return f"""# {_one_line(spec['name'])}

Status: local scaffold; not registered, deployed, scheduled, or running.

## Mission

{_one_line(spec['objective']['statement'])}

- Beneficiaries: {_one_line(spec['objective']['beneficiaries'])}
- Decision use: {_one_line(spec['objective']['decision_use'])}
- Forecasting for Good rationale: {_one_line(spec['forecasting_for_good']['rationale'])}

## Forecast contract

- Targets: {', '.join(_one_line(value) for value in spec['targets'])}
- Horizon: {_one_line(spec['horizon'])}
- Outcome: {outcome_text}
- Submission policy: {spec['operation']['submission_policy']}
- Maximum credits per run: {spec['operation']['max_credits_per_run']}

Before every forecast, read the live HeadlineArena challenge and settlement contract. Treat its target, deadline, outcome schema, and resolution rules as authoritative. Never invent a missing contract field or hard-code a market neutral band.

## Evidence policy

Allowed sources:
{_bullets(spec['data']['allowed_sources'])}

Excluded sources:
{excluded}

Freshness requirement: {_one_line(spec['data']['freshness'])}

Keep observations, source provenance, assumptions, and probabilistic conclusions distinguishable. Report meaningful uncertainty and counter-evidence.

## Operation

Cadence: {_one_line(spec['schedule']['cadence'])}

Triggers:
{triggers}

This cadence records deployment intent; it is not evidence that a scheduler exists. Use the installed HeadlineArena plugin for live discovery and supported operations. Require the configured review policy before external submissions, and stop with a clear user-facing message if required credit is unavailable.

## Evaluation

- Primary metric: {_one_line(spec['evaluation']['primary_metric'])}
- Review cadence: {_one_line(spec['evaluation']['review_cadence'])}
- Minimum resolved forecasts before calibration conclusions: {spec['evaluation']['minimum_resolved_forecasts']}

Store the question and contract version, evidence available at forecast time, probability distribution, revisions, model metadata, resolution, and score so results remain auditable.
"""


def scaffold(spec_path, workspace_path, output_arg, force=False, created_at=None):
    spec_path = Path(spec_path)
    if spec_path.stat().st_size > MAX_SPEC_BYTES:
        raise SpecError("spec file exceeds the 1 MiB limit")
    try:
        raw = json.loads(
            spec_path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise SpecError(f"spec is not valid JSON: {exc}")
    spec = validate_spec(raw)

    workspace = Path(workspace_path)
    if not workspace.is_dir():
        raise SpecError("--workspace must be an existing directory")
    relative_output = output_arg or f"forecast-agents/{_slug(spec['name'])}"
    output = _inside_workspace(workspace, relative_output)
    spec_file = output / "forecast-agent.json"
    instructions_file = output / "AGENT.md"

    if output.exists() and not force:
        raise SpecError(
            f"output already exists: {output}; "
            "pass --force only after explicit approval"
        )
    if output.exists() and not output.is_dir():
        raise SpecError(f"output exists and is not a directory: {output}")
    if force:
        for generated in (spec_file, instructions_file):
            if generated.is_symlink() or (generated.exists() and not generated.is_file()):
                raise SpecError(f"refusing to replace symlink or non-file path: {generated}")

    timestamp = created_at or (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp):
        raise SpecError("--created-at must use UTC format YYYY-MM-DDTHH:MM:SSZ")
    rendered_spec = {
        **spec,
        "audit": {
            "status": "draft",
            "created_at": timestamp,
            "builder": "headlinearena-agent-plugin/ha-build-agent",
            "builder_version": BUILDER_VERSION,
        },
    }
    spec_text = json.dumps(rendered_spec, indent=2, ensure_ascii=False) + "\n"
    instructions_text = render_instructions(spec)

    output.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(spec_text, encoding="utf-8")
    instructions_file.write_text(instructions_text, encoding="utf-8")
    return {
        "status": "created",
        "output_directory": str(output),
        "files": [str(spec_file), str(instructions_file)],
        "network_calls": 0,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, help="Path to the approved JSON specification")
    parser.add_argument(
        "--workspace",
        default=".",
        help="Existing workspace root (default: current directory)",
    )
    parser.add_argument(
        "--output",
        help="Relative output directory (default: forecast-agents/<agent-slug>)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace only the two generated files in an existing directory",
    )
    parser.add_argument("--created-at", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        result = scaffold(args.spec, args.workspace, args.output, args.force, args.created_at)
    except (OSError, SpecError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
