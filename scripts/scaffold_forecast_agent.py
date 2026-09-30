#!/usr/bin/env python3
"""Create a local, auditable forecasting-agent scaffold from validated JSON.

This script is intentionally offline and stdlib-only. It does not register,
deploy, schedule, fund, or run an agent, and it never calls HeadlineArena APIs.
"""

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import stat
import sys
from decimal import Decimal
from pathlib import Path


SCHEMA_VERSION = 1
BUILDER_VERSION = "1.39.0"
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
        _known_fields(
            outcome, "outcome", {"type", "labels", "positive_label"}
        )
        labels = _string_list(
            outcome.get("labels"), "outcome.labels", exact_items=2
        )
        positive_label = _text(
            outcome.get("positive_label"), "outcome.positive_label", max_length=300
        )
        if positive_label not in labels:
            raise SpecError(
                "outcome.positive_label must exactly match one outcome.labels value"
            )
        return {
            "type": outcome_type,
            "labels": labels,
            "positive_label": positive_label,
        }
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
    if (
        type(spec["schema_version"]) is not int
        or spec["schema_version"] != SCHEMA_VERSION
    ):
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


def _relative_output_parts(relative_output):
    output_arg = Path(relative_output)
    if output_arg.is_absolute() or ".." in output_arg.parts:
        raise SpecError("--output must be a relative path without '..'")
    parts = tuple(part for part in output_arg.parts if part != ".")
    if not parts:
        raise SpecError("--output must not be the workspace root")
    return parts


def _one_line(value):
    return " ".join(value.split())


def _bullets(values):
    return "\n".join(f"- {_one_line(value)}" for value in values) or "- None specified"


def _reject_json_constant(value):
    raise SpecError(f"spec contains non-finite JSON number: {value}")


def _canonical_number(value):
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        raise SpecError("spec contains a non-finite JSON number")
    decimal = Decimal(repr(value))
    if decimal == 0:
        return "0"
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _canonical_json(value):
    """Serialize JSON with sorted keys and exponent-free normalized numbers."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _canonical_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise SpecError("spec object keys must be strings")
        return "{" + ",".join(
            f"{_canonical_json(key)}:{_canonical_json(value[key])}"
            for key in sorted(value)
        ) + "}"
    raise SpecError(f"spec contains unsupported JSON value: {type(value).__name__}")


def canonical_spec_bytes(spec):
    """Return cross-runtime canonical bytes for a normalized v1 specification."""
    return _canonical_json(spec).encode("utf-8")


def canonical_spec_digest(spec):
    """Return the stable digest of a validated, normalized v1 specification."""
    return f"sha256:{hashlib.sha256(canonical_spec_bytes(spec)).hexdigest()}"


def _file_manifest_entry(relative_path, media_type, content):
    encoded = content.encode("utf-8")
    return {
        "path": relative_path,
        "media_type": media_type,
        "digest": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        "size_bytes": len(encoded),
    }


def validate_utc_timestamp(value, path="--created-at"):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise SpecError(f"{path} must use UTC format YYYY-MM-DDTHH:MM:SSZ")
    try:
        dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise SpecError(f"{path} is not a valid UTC timestamp") from exc
    return value


def render_instructions(spec):
    outcome = spec["outcome"]
    if outcome["type"] == "numeric":
        outcome_text = f"numeric ({outcome['unit']}; {outcome['encoding']} encoding)"
    elif outcome["type"] == "binary":
        outcome_text = (
            f"binary: {', '.join(outcome['labels'])} "
            f"(positive: {outcome['positive_label']})"
        )
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


def _supports_secure_dirfd():
    """Return whether this runtime can perform fail-closed dirfd writes."""
    return (
        os.name == "posix"
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and os.mkdir in os.supports_dir_fd
        and os.rmdir in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
    )


def _same_directory(pinned, current):
    return (
        stat.S_ISDIR(current.st_mode)
        and current.st_dev == pinned.st_dev
        and current.st_ino == pinned.st_ino
    )


def _verify_pinned_root(directory_fd, root):
    """Ensure the workspace path still names the pinned root directory."""
    try:
        current = root.lstat()
    except FileNotFoundError:
        raise SpecError("workspace directory changed during scaffold creation")
    if not _same_directory(os.fstat(directory_fd), current):
        raise SpecError("workspace directory changed during scaffold creation")


def _verify_pinned_entry(directory_fd, parent_fd, entry_name):
    """Apply lstat semantics relative to the already pinned parent fd."""
    try:
        current = os.stat(entry_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise SpecError("output ancestry changed during scaffold creation")
    if not _same_directory(os.fstat(directory_fd), current):
        raise SpecError("output ancestry changed during scaffold creation")


def _verify_pinned_chain(workspace_fd, workspace, edges):
    _verify_pinned_root(workspace_fd, workspace)
    for directory_fd, parent_fd, entry_name in edges:
        _verify_pinned_entry(directory_fd, parent_fd, entry_name)


def _open_pinned_workspace(workspace):
    if not _supports_secure_dirfd():
        raise SpecError(
            "secure directory-relative writes are unavailable on this platform; "
            "refusing to scaffold"
        )
    try:
        directory_fd = os.open(
            str(workspace), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
    except OSError as exc:
        raise SpecError(f"could not securely open workspace directory: {exc}")
    try:
        _verify_pinned_root(directory_fd, workspace)
    except Exception:
        os.close(directory_fd)
        raise
    return directory_fd


def _open_child_directory(parent_fd, entry_name):
    try:
        child_fd = os.open(
            entry_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise SpecError(
            f"output component is not a safe directory: {entry_name}: {exc}"
        ) from exc
    try:
        _verify_pinned_entry(child_fd, parent_fd, entry_name)
    except Exception:
        os.close(child_fd)
        raise
    return child_fd


def _unlink_if_present(directory_fd, entry_name):
    try:
        os.unlink(entry_name, dir_fd=directory_fd)
    except FileNotFoundError:
        pass


def _write_new_file(directory_fd, target_name, content):
    descriptor = os.open(
        target_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        try:
            stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
        except Exception:
            os.close(descriptor)
            raise
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        _unlink_if_present(directory_fd, target_name)
        raise


def _create_files(directory_fd, files):
    """Create a complete two-file scaffold inside a newly created directory.

    All names are opened with O_EXCL relative to the pinned output fd. On any
    failure, every already-created generated file is removed before the error
    escapes.
    """
    files = tuple(files)
    created = []
    try:
        for target_name, content in files:
            _write_new_file(directory_fd, target_name, content)
            created.append(target_name)
        os.fsync(directory_fd)
    except Exception:
        for target_name in created:
            _unlink_if_present(directory_fd, target_name)
        raise


def _remove_created_directories(created_entries):
    """Remove newly created empty directories, deepest first."""
    for parent_fd, entry_name in reversed(created_entries):
        try:
            os.rmdir(entry_name, dir_fd=parent_fd)
        except OSError:
            # An attacker may have renamed or replaced this path. The pinned
            # child has already been emptied; never follow or delete the
            # replacement, and do not mask the original failure.
            pass


def scaffold_data(raw_spec, workspace_path, output_arg=None, created_at=None):
    """Validate an in-memory approved spec and create its local scaffold."""
    spec = validate_spec(raw_spec)

    workspace = Path(workspace_path).resolve()
    if not workspace.is_dir():
        raise SpecError("--workspace must be an existing directory")
    relative_output = output_arg or f"forecast-agents/{_slug(spec['name'])}"
    output_parts = _relative_output_parts(relative_output)
    output = workspace.joinpath(*output_parts)
    if not _supports_secure_dirfd():
        raise SpecError(
            "secure directory-relative writes are unavailable on this platform; "
            "refusing to scaffold"
        )
    target_names = ("forecast-agent.json", "AGENT.md")
    spec_file = output / target_names[0]
    instructions_file = output / target_names[1]

    timestamp = created_at or (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    timestamp = validate_utc_timestamp(timestamp)
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
    relative_output = "/".join(output_parts)
    generated_files = [
        _file_manifest_entry(
            f"{relative_output}/{target_names[0]}",
            "application/json",
            spec_text,
        ),
        _file_manifest_entry(
            f"{relative_output}/{target_names[1]}",
            "text/markdown",
            instructions_text,
        ),
    ]

    workspace_fd = _open_pinned_workspace(workspace)
    open_fds = [workspace_fd]
    edges = []
    created_parents = []
    output_created = False
    output_fd = None
    try:
        parent_fd = workspace_fd
        for component in output_parts[:-1]:
            try:
                child_fd = _open_child_directory(parent_fd, component)
            except SpecError as exc:
                cause = exc.__cause__
                if not isinstance(cause, FileNotFoundError):
                    raise
                try:
                    os.mkdir(component, 0o755, dir_fd=parent_fd)
                    created_parents.append((parent_fd, component))
                except FileExistsError:
                    pass
                child_fd = _open_child_directory(parent_fd, component)
            open_fds.append(child_fd)
            edges.append((child_fd, parent_fd, component))
            parent_fd = child_fd

        output_name = output_parts[-1]
        try:
            os.mkdir(output_name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            raise SpecError(
                f"output already exists: {output}; choose a new output path"
            )
        output_created = True
        output_fd = _open_child_directory(parent_fd, output_name)
        open_fds.append(output_fd)
        edges.append((output_fd, parent_fd, output_name))
        _verify_pinned_chain(workspace_fd, workspace, edges)

        _create_files(
            output_fd,
            ((target_names[0], spec_text), (target_names[1], instructions_text)),
        )
        _verify_pinned_chain(workspace_fd, workspace, edges)
    except Exception:
        if output_fd is not None:
            for target_name in target_names:
                _unlink_if_present(output_fd, target_name)
        if output_created:
            try:
                os.rmdir(output_parts[-1], dir_fd=parent_fd)
            except OSError:
                # Preserve the original error if the directory entry was
                # concurrently renamed/replaced; generated files are already
                # removed through the pinned output fd.
                pass
        _remove_created_directories(created_parents)
        raise
    finally:
        for directory_fd in reversed(open_fds):
            os.close(directory_fd)
    return {
        "status": "created",
        "output_directory": str(output),
        "files": [str(spec_file), str(instructions_file)],
        "network_calls": 0,
        "spec_digest": canonical_spec_digest(spec),
        "relative_output_directory": relative_output,
        "generated_files": generated_files,
        "created_at": timestamp,
    }


def scaffold(spec_path, workspace_path, output_arg, created_at=None):
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
    return scaffold_data(raw, workspace_path, output_arg, created_at)


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
    parser.add_argument("--created-at", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        result = scaffold(args.spec, args.workspace, args.output, args.created_at)
    except (OSError, SpecError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
