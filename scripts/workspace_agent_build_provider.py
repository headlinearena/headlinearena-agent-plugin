#!/usr/bin/env python3
"""Run one offline Workspace AgentDraft provider from a bounded stdin request.

The caller supplies only trusted filesystem locations on the command line. The
customer build envelope is accepted exclusively on stdin, and plugin identity is
derived from the immutable bundle rather than from request-controlled metadata.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

import scaffold_forecast_agent as scaffold
import workspace_agent_build as workspace_build


DEFAULT_BUNDLE_ROOT = Path(__file__).resolve().parent.parent
MAX_STDIN_BYTES = workspace_build.MAX_REQUEST_BYTES
MAX_RESULT_BYTES = 64 * 1024
MAX_BUNDLE_METADATA_BYTES = 4096
MAX_MANIFEST_BYTES = 128 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_BUNDLE_FIELDS = {
    "name",
    "version",
    "commit",
    "manifest_sha256",
    "archive_sha256",
    "required_skill",
    "archive_verified",
}


class ProviderEntrypointError(RuntimeError):
    """An internal failure represented externally by a stable, non-secret code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _StableArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        del message
        raise ProviderEntrypointError("invalid_arguments")


def _reject_json_constant(value):
    del value
    raise ProviderEntrypointError("invalid_request")


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProviderEntrypointError("invalid_request")
        result[key] = value
    return result


def _parse_json_bytes(payload, *, error_code):
    try:
        text = payload.decode("utf-8")
        return json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except ProviderEntrypointError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProviderEntrypointError(error_code) from None


def _read_bounded(stream, maximum, *, empty_code, oversized_code):
    payload = stream.read(maximum + 1)
    if not isinstance(payload, bytes):
        raise ProviderEntrypointError(empty_code)
    if not payload:
        raise ProviderEntrypointError(empty_code)
    if len(payload) > maximum:
        raise ProviderEntrypointError(oversized_code)
    return payload


def _read_regular_file(path, maximum):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError:
        raise ProviderEntrypointError("invalid_bundle_identity") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise ProviderEntrypointError("invalid_bundle_identity")
        chunks = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise ProviderEntrypointError("invalid_bundle_identity")
            chunks.append(chunk)
        if size == 0:
            raise ProviderEntrypointError("invalid_bundle_identity")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def trusted_plugin_snapshot(bundle_root=DEFAULT_BUNDLE_ROOT):
    """Derive all six attestation fields from the verified, read-only bundle."""

    root = Path(bundle_root)
    metadata_bytes = _read_regular_file(
        root / "BUNDLED_BUILD.json", MAX_BUNDLE_METADATA_BYTES
    )
    manifest_bytes = _read_regular_file(
        root / ".codex-plugin" / "plugin.json", MAX_MANIFEST_BYTES
    )
    try:
        metadata = _parse_json_bytes(
            metadata_bytes, error_code="invalid_bundle_identity"
        )
        manifest = _parse_json_bytes(
            manifest_bytes, error_code="invalid_bundle_identity"
        )
    except ProviderEntrypointError:
        raise ProviderEntrypointError("invalid_bundle_identity") from None
    if (
        not isinstance(metadata, dict)
        or set(metadata) != _BUNDLE_FIELDS
        or not isinstance(manifest, dict)
    ):
        raise ProviderEntrypointError("invalid_bundle_identity")

    name = metadata.get("name")
    version = metadata.get("version")
    commit = metadata.get("commit")
    manifest_sha256 = metadata.get("manifest_sha256")
    archive_sha256 = metadata.get("archive_sha256")
    required_skill = metadata.get("required_skill")
    actual_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if (
        metadata.get("archive_verified") is not True
        or name != workspace_build.PLUGIN_NAME
        or version != workspace_build.PLUGIN_VERSION
        or commit == "0" * 40
        or not isinstance(commit, str)
        or _COMMIT.fullmatch(commit) is None
        or manifest_sha256 == "0" * 64
        or not isinstance(manifest_sha256, str)
        or _SHA256.fullmatch(manifest_sha256) is None
        or manifest_sha256 != actual_manifest_sha256
        or archive_sha256 == "0" * 64
        or not isinstance(archive_sha256, str)
        or _SHA256.fullmatch(archive_sha256) is None
        or required_skill != workspace_build.PLUGIN_SKILL
        or manifest.get("name") != name
        or manifest.get("version") != version
    ):
        raise ProviderEntrypointError("invalid_bundle_identity")

    skill_path = root / "skills" / required_skill / "SKILL.md"
    _read_regular_file(skill_path, MAX_MANIFEST_BYTES)
    return {
        "name": name,
        "version": version,
        "commit_sha": commit,
        "skill": required_skill,
        "manifest_digest": f"sha256:{manifest_sha256}",
        "archive_digest": f"sha256:{archive_sha256}",
    }


def load_stdin_request(stream):
    payload = _read_bounded(
        stream,
        MAX_STDIN_BYTES,
        empty_code="invalid_request",
        oversized_code="request_too_large",
    )
    return _parse_json_bytes(payload, error_code="invalid_request")


def run_provider(
    stream, *, workspace_root, bundle_root=DEFAULT_BUNDLE_ROOT, produced_at=None
):
    request = load_stdin_request(stream)
    trusted_snapshot = trusted_plugin_snapshot(bundle_root)
    try:
        return workspace_build.build_agent_draft(
            request,
            workspace_root,
            trusted_snapshot,
            produced_at,
        )
    except workspace_build.ContractError:
        raise ProviderEntrypointError("invalid_request") from None
    except scaffold.SpecError:
        raise ProviderEntrypointError("invalid_spec") from None
    except OSError:
        raise ProviderEntrypointError("provider_io_error") from None


def _result_bytes(result):
    try:
        payload = (
            json.dumps(
                result, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ProviderEntrypointError("invalid_provider_result") from None
    if len(payload) > MAX_RESULT_BYTES:
        raise ProviderEntrypointError("result_too_large")
    return payload


def parse_args(argv=None):
    parser = _StableArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--request-stdin", action="store_true", required=True)
    parser.add_argument(
        "--workspace",
        required=True,
        help="Trusted Workspace/staging root; never sourced from the stdin request",
    )
    parser.add_argument(
        "--bundle-root",
        default=os.fspath(DEFAULT_BUNDLE_ROOT),
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv=None, *, stdin=None, stdout=None, stderr=None):
    input_stream = stdin or sys.stdin.buffer
    output_stream = stdout or sys.stdout.buffer
    error_stream = stderr or sys.stderr
    try:
        args = parse_args(argv)
        result = run_provider(
            input_stream,
            workspace_root=args.workspace,
            bundle_root=args.bundle_root,
        )
        payload = _result_bytes(result)
        output_stream.write(payload)
        output_stream.flush()
    except ProviderEntrypointError as exc:
        error_stream.write(f"{exc.code}\n")
        return 1
    except Exception:
        error_stream.write("provider_failed\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
