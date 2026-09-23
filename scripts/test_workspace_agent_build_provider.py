#!/usr/bin/env python3
"""Security and contract tests for the hosted stdin AgentDraft provider."""

import copy
import hashlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import workspace_agent_build_provider as stdin_provider


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "tests/fixtures/workspace_agent_build_v1.json"


class WorkspaceAgentBuildStdinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "trusted-workspace"
        self.bundle = self.root / "trusted-bundle"
        self.workspace.mkdir()
        (self.bundle / ".codex-plugin").mkdir(parents=True)
        (self.bundle / "skills" / "ha-build-agent").mkdir(parents=True)

        manifest = (ROOT / ".codex-plugin" / "plugin.json").read_bytes()
        (self.bundle / ".codex-plugin" / "plugin.json").write_bytes(manifest)
        (self.bundle / "skills" / "ha-build-agent" / "SKILL.md").write_text(
            "---\nname: ha-build-agent\n---\n",
            encoding="utf-8",
        )
        self.snapshot = {
            "name": "headlinearena-agent-plugin",
            "version": "1.36.0",
            "commit_sha": "0123456789abcdef0123456789abcdef01234567",
            "skill": "ha-build-agent",
            "manifest_digest": f"sha256:{hashlib.sha256(manifest).hexdigest()}",
            "archive_digest": f"sha256:{'2' * 64}",
        }
        metadata = {
            "name": self.snapshot["name"],
            "version": self.snapshot["version"],
            "commit": self.snapshot["commit_sha"],
            "manifest_sha256": self.snapshot["manifest_digest"][len("sha256:") :],
            "archive_sha256": self.snapshot["archive_digest"][len("sha256:") :],
            "required_skill": self.snapshot["skill"],
            "archive_verified": True,
        }
        (self.bundle / "BUNDLED_BUILD.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.request = fixture["request"]
        self.request["plugin_snapshot"] = copy.deepcopy(self.snapshot)
        self.produced_at = fixture["produced_at"]

    def request_bytes(self, request=None):
        value = self.request if request is None else request
        return json.dumps(value).encode("utf-8")

    def invoke_provider(self, payload=None):
        return stdin_provider.run_provider(
            io.BytesIO(self.request_bytes() if payload is None else payload),
            workspace_root=self.workspace,
            bundle_root=self.bundle,
            produced_at=self.produced_at,
        )

    def test_builds_from_stdin_with_bundle_derived_identity_and_stays_offline(self):
        with mock.patch.object(
            urllib.request,
            "urlopen",
            side_effect=AssertionError("provider must remain offline"),
        ), mock.patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("provider must remain offline"),
        ):
            result = self.invoke_provider()

        self.assertEqual(result["plugin_snapshot"], self.snapshot)
        self.assertEqual(result["request_id"], self.request["request_id"])
        self.assertEqual(
            {Path(item["path"]).name for item in result["draft"]["generated_files"]},
            {"AGENT.md", "forecast-agent.json"},
        )
        self.assertTrue(
            (self.workspace / "forecast-agents/gold-macro-monitor/AGENT.md").is_file()
        )

    def test_rejects_oversized_stdin_before_json_parsing(self):
        with self.assertRaisesRegex(
            stdin_provider.ProviderEntrypointError, "request_too_large"
        ):
            self.invoke_provider(b" " * (stdin_provider.MAX_STDIN_BYTES + 1))
        self.assertFalse((self.workspace / "forecast-agents").exists())

    def test_rejects_malformed_trailing_and_non_finite_json(self):
        payloads = (
            b"",
            b'{"broken":',
            self.request_bytes() + b'{"second":true}',
            self.request_bytes().replace(
                b'"schema_version": 1', b'"schema_version": NaN'
            ),
            b'{"duplicate":1,"duplicate":2}',
        )
        for payload in payloads:
            with self.subTest(payload=payload[-24:]):
                with self.assertRaisesRegex(
                    stdin_provider.ProviderEntrypointError, "invalid_request"
                ):
                    self.invoke_provider(payload)
        self.assertFalse((self.workspace / "forecast-agents").exists())

    def test_rejects_request_snapshot_spoofing(self):
        request = copy.deepcopy(self.request)
        request["plugin_snapshot"]["commit_sha"] = "f" * 40
        with self.assertRaisesRegex(
            stdin_provider.ProviderEntrypointError, "invalid_request"
        ):
            self.invoke_provider(self.request_bytes(request))
        self.assertFalse((self.workspace / "forecast-agents").exists())

    def test_main_emits_one_compact_success_json_and_no_stderr(self):
        stdout = io.BytesIO()
        stderr = io.StringIO()
        exit_code = stdin_provider.main(
            [
                "--request-stdin",
                "--workspace",
                os.fspath(self.workspace),
                "--bundle-root",
                os.fspath(self.bundle),
            ],
            stdin=io.BytesIO(self.request_bytes()),
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(stdout.getvalue().count(b"\n"), 1)
        self.assertEqual(
            json.loads(stdout.getvalue())["plugin_snapshot"], self.snapshot
        )

    def test_cli_has_no_request_file_argument(self):
        stderr = io.StringIO()
        exit_code = stdin_provider.main(
            [
                "--request",
                "/tmp/customer-request.json",
                "--workspace",
                os.fspath(self.workspace),
            ],
            stdin=io.BytesIO(self.request_bytes()),
            stdout=io.BytesIO(),
            stderr=stderr,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr.getvalue(), "invalid_arguments\n")

    def test_rejects_oversized_result_before_writing_stdout(self):
        with self.assertRaisesRegex(
            stdin_provider.ProviderEntrypointError, "result_too_large"
        ):
            stdin_provider._result_bytes(
                {"result": "x" * stdin_provider.MAX_RESULT_BYTES}
            )

    def test_error_is_a_stable_code_and_never_echoes_customer_secret(self):
        request = copy.deepcopy(self.request)
        customer_secret = "super-sensitive-customer-value"
        request["draft"]["spec"]["extensions"] = {
            "openai_api_key_value": customer_secret
        }
        stdout = io.BytesIO()
        stderr = io.StringIO()
        exit_code = stdin_provider.main(
            [
                "--request-stdin",
                "--workspace",
                os.fspath(self.workspace),
                "--bundle-root",
                os.fspath(self.bundle),
            ],
            stdin=io.BytesIO(self.request_bytes(request)),
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), b"")
        self.assertEqual(stderr.getvalue(), "invalid_request\n")
        self.assertNotIn(customer_secret, stderr.getvalue())

    def test_rejects_non_ascii_confusable_extension_key(self):
        request = copy.deepcopy(self.request)
        request["draft"]["spec"]["extensions"] = {"api_кey": "hidden"}
        with self.assertRaisesRegex(
            stdin_provider.ProviderEntrypointError, "invalid_request"
        ):
            self.invoke_provider(self.request_bytes(request))
        self.assertFalse((self.workspace / "forecast-agents").exists())

    def test_bundle_identity_is_computed_not_accepted_from_the_request(self):
        metadata_path = self.bundle / "BUNDLED_BUILD.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["manifest_sha256"] = "3" * 64
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(
            stdin_provider.ProviderEntrypointError, "invalid_bundle_identity"
        ):
            self.invoke_provider()
        self.assertFalse((self.workspace / "forecast-agents").exists())

    def test_workspace_ref_cannot_select_the_filesystem_root(self):
        request = copy.deepcopy(self.request)
        request["workspace_ref"] = "../../attacker-controlled"
        result = self.invoke_provider(self.request_bytes(request))
        self.assertEqual(
            result["draft"]["output_directory"],
            "forecast-agents/gold-macro-monitor",
        )
        self.assertTrue(
            (self.workspace / "forecast-agents/gold-macro-monitor/AGENT.md").is_file()
        )
        self.assertFalse((self.root / "attacker-controlled").exists())


if __name__ == "__main__":
    unittest.main()
