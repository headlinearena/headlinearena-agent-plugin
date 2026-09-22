#!/usr/bin/env python3
"""Build an AgentDraft from the versioned hosted Workspace envelope.

The trusted runtime supplies the filesystem workspace separately. ``workspace_ref``
is only an opaque control-plane reference and is never interpreted as a path. This
adapter is offline and delegates all writes to the existing secure scaffolder.
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path, PurePosixPath

import scaffold_forecast_agent as scaffold


CONTRACT_VERSION = "workspace-conversation-v1"
TASK_KIND = "agent_draft"
PLUGIN_NAME = "headlinearena-agent-plugin"
PLUGIN_VERSION = scaffold.BUILDER_VERSION
PLUGIN_SKILL = "ha-build-agent"
MAX_REQUEST_BYTES = 2 * 1024 * 1024

REQUEST_FIELDS = {
    "contract_version",
    "request_id",
    "idempotency_key",
    "workspace_ref",
    "task_kind",
    "plugin_snapshot",
    "draft",
}
PLUGIN_SNAPSHOT_FIELDS = {
    "name",
    "version",
    "commit_sha",
    "skill",
    "manifest_digest",
    "archive_digest",
}
DRAFT_FIELDS = {"draft_id", "version", "spec", "output_directory"}
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
FORBIDDEN_KEYS = {
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "clientsecret",
    "credential",
    "credentials",
    "deploy",
    "deployed",
    "deployment",
    "execution_mode",
    "executionmode",
    "password",
    "private_key",
    "privatekey",
    "refresh_token",
    "refreshtoken",
    "register",
    "registered",
    "registration",
    "secret",
    "token",
}


class ContractError(ValueError):
    """Raised when a Workspace AgentDraft envelope is invalid."""


def _mapping(value, path):
    if not isinstance(value, dict):
        raise ContractError(f"{path} must be an object")
    return value


def _exact_fields(value, path, expected):
    obj = _mapping(value, path)
    missing = sorted(expected - set(obj))
    unknown = sorted(set(obj) - expected)
    if missing:
        raise ContractError(f"{path} is missing required field(s): {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{path} has unknown field(s): {', '.join(unknown)}")
    return obj


def _text(value, path, *, max_length=512):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{path} must be a non-empty string")
    if value != value.strip():
        raise ContractError(f"{path} must not have leading or trailing whitespace")
    if len(value) > max_length:
        raise ContractError(f"{path} must be at most {max_length} characters")
    if any(ord(char) < 32 for char in value):
        raise ContractError(f"{path} contains unsupported control characters")
    return value


def _reject_forbidden_keys(value, path="request"):
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in FORBIDDEN_KEYS:
                raise ContractError(f"{path}.{key} is not allowed in a build envelope")
            _reject_forbidden_keys(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_forbidden_keys(nested, f"{path}[{index}]")


def _digest(value, path):
    value = _text(value, path, max_length=71)
    if not DIGEST_PATTERN.fullmatch(value):
        raise ContractError(f"{path} must be sha256:<64 lowercase hex characters>")
    return value


def _relative_output(value):
    value = _text(value, "request.draft.output_directory", max_length=512)
    if "\\" in value:
        raise ContractError("request.draft.output_directory must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractError(
            "request.draft.output_directory must be a normalized relative path"
        )
    normalized = "/".join(path.parts)
    if normalized != value:
        raise ContractError(
            "request.draft.output_directory must be a normalized relative path"
        )
    return normalized


def _plugin_snapshot(value):
    snapshot = _exact_fields(
        value, "request.plugin_snapshot", PLUGIN_SNAPSHOT_FIELDS
    )
    name = _text(snapshot["name"], "request.plugin_snapshot.name")
    version = _text(snapshot["version"], "request.plugin_snapshot.version")
    skill = _text(snapshot["skill"], "request.plugin_snapshot.skill")
    if (
        name != PLUGIN_NAME
        or version != PLUGIN_VERSION
        or skill != PLUGIN_SKILL
    ):
        raise ContractError(
            "request.plugin_snapshot does not identify this plugin and skill version"
        )
    commit_sha = _text(
        snapshot["commit_sha"], "request.plugin_snapshot.commit_sha", max_length=40
    )
    if not COMMIT_PATTERN.fullmatch(commit_sha):
        raise ContractError(
            "request.plugin_snapshot.commit_sha must be 40 lowercase hex characters"
        )
    return {
        "name": name,
        "version": version,
        "commit_sha": commit_sha,
        "skill": skill,
        "manifest_digest": _digest(
            snapshot["manifest_digest"], "request.plugin_snapshot.manifest_digest"
        ),
        "archive_digest": _digest(
            snapshot["archive_digest"], "request.plugin_snapshot.archive_digest"
        ),
    }


def validate_request(raw_request):
    request = _exact_fields(raw_request, "request", REQUEST_FIELDS)
    _reject_forbidden_keys(request)
    if request["contract_version"] != CONTRACT_VERSION:
        raise ContractError(
            f"request.contract_version must be {CONTRACT_VERSION}"
        )
    if request["task_kind"] != TASK_KIND:
        raise ContractError(f"request.task_kind must be {TASK_KIND}")

    draft = _exact_fields(request["draft"], "request.draft", DRAFT_FIELDS)
    version = draft["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ContractError("request.draft.version must be a positive integer")
    spec = _mapping(draft["spec"], "request.draft.spec")

    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": _text(request["request_id"], "request.request_id"),
        "idempotency_key": _text(
            request["idempotency_key"], "request.idempotency_key"
        ),
        "workspace_ref": _text(request["workspace_ref"], "request.workspace_ref"),
        "task_kind": TASK_KIND,
        "plugin_snapshot": _plugin_snapshot(request["plugin_snapshot"]),
        "draft": {
            "draft_id": _text(draft["draft_id"], "request.draft.draft_id"),
            "version": version,
            "spec": spec,
            "output_directory": _relative_output(draft["output_directory"]),
        },
    }


def _timestamp(produced_at=None):
    value = produced_at or (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise ContractError("produced_at must use UTC format YYYY-MM-DDTHH:MM:SSZ")
    return value


def build_agent_draft(raw_request, workspace_path, produced_at=None):
    """Validate one request and create a draft through the shared scaffolder."""
    request = validate_request(raw_request)
    timestamp = _timestamp(produced_at)
    draft = request["draft"]
    scaffold_result = scaffold.scaffold_data(
        draft["spec"],
        workspace_path,
        draft["output_directory"],
        created_at=timestamp,
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": request["request_id"],
        "task_kind": TASK_KIND,
        "plugin_snapshot": request["plugin_snapshot"],
        "draft": {
            "draft_id": draft["draft_id"],
            "version": draft["version"],
            "status": "draft",
            "spec_digest": scaffold_result["spec_digest"],
            "output_directory": scaffold_result["relative_output_directory"],
            "generated_files": scaffold_result["generated_files"],
        },
        "produced_at": timestamp,
    }


def _reject_json_constant(value):
    raise ContractError(f"request contains non-finite JSON number: {value}")


def load_request(request_path):
    path = Path(request_path)
    if path.stat().st_size > MAX_REQUEST_BYTES:
        raise ContractError("request file exceeds the 2 MiB limit")
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise ContractError(f"request is not valid JSON: {exc}") from exc


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="AgentDraft request JSON")
    parser.add_argument(
        "--workspace",
        required=True,
        help="Trusted tenant Workspace root; never sourced from workspace_ref",
    )
    parser.add_argument("--produced-at", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        result = build_agent_draft(
            load_request(args.request), args.workspace, args.produced_at
        )
    except (ContractError, scaffold.SpecError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
