#!/usr/bin/env python3
"""Contract tests for the hosted Workspace AgentDraft build adapter."""

import copy
import hashlib
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path, PurePosixPath
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import scaffold_forecast_agent as scaffold
import workspace_agent_build as workspace_build


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "tests/fixtures/workspace_agent_build_v1.json"


def fixture_data():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class WorkspaceAgentBuildContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / "trusted-tenant-workspace"
        self.workspace.mkdir()
        self.fixture = fixture_data()

    def build(self, request=None):
        return workspace_build.build_agent_draft(
            request or copy.deepcopy(self.fixture["request"]),
            self.workspace,
            self.fixture["produced_at"],
        )

    def test_provider_fixture_matches_exact_result_and_file_bytes(self):
        with mock.patch.object(
            urllib.request,
            "urlopen",
            side_effect=AssertionError("AgentDraft build must remain offline"),
        ), mock.patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("AgentDraft build must remain offline"),
        ):
            result = self.build()

        self.assertEqual(result, self.fixture["expected_result"])
        self.assertNotIn("network_calls", result)
        self.assertNotIn("idempotency_key", result)
        for entry in result["draft"]["generated_files"]:
            relative_path = PurePosixPath(entry["path"])
            self.assertFalse(relative_path.is_absolute())
            self.assertEqual(
                relative_path.parts[:3],
                ("forecast-agents", "gold-macro-monitor", relative_path.name),
            )
            payload = (self.workspace / Path(*relative_path.parts)).read_bytes()
            self.assertEqual(entry["size_bytes"], len(payload))
            self.assertEqual(
                entry["digest"], f"sha256:{hashlib.sha256(payload).hexdigest()}"
            )

    def test_delegates_writes_to_the_existing_secure_scaffolder(self):
        with mock.patch.object(
            workspace_build.scaffold,
            "scaffold_data",
            wraps=scaffold.scaffold_data,
        ) as scaffold_data:
            self.build()
        scaffold_data.assert_called_once()
        _, workspace, output = scaffold_data.call_args.args[:3]
        self.assertEqual(workspace, self.workspace)
        self.assertEqual(output, "forecast-agents/gold-macro-monitor")

    def test_workspace_ref_is_opaque_and_never_used_as_the_filesystem_root(self):
        request = copy.deepcopy(self.fixture["request"])
        request["workspace_ref"] = "../../not-the-trusted-workspace"
        result = self.build(request)
        self.assertEqual(
            result["draft"]["output_directory"],
            "forecast-agents/gold-macro-monitor",
        )
        self.assertTrue(
            (self.workspace / "forecast-agents/gold-macro-monitor/AGENT.md").is_file()
        )
        self.assertNotIn("workspace_ref", result)

    def test_rejects_credentials_modes_and_lifecycle_claims_in_request(self):
        for key in (
            "client_secret",
            "accessToken",
            "execution_mode",
            "deployed",
            "registration",
        ):
            with self.subTest(key=key):
                request = copy.deepcopy(self.fixture["request"])
                request["draft"]["spec"]["extensions"][key] = "not-allowed"
                with self.assertRaisesRegex(
                    workspace_build.ContractError, "not allowed"
                ):
                    workspace_build.validate_request(request)

    def test_requires_exact_plugin_snapshot_and_lowercase_wire_enums(self):
        mutations = [
            ("task_kind", "AGENT_DRAFT"),
            ("plugin_snapshot.commit_sha", "762f693"),
            (
                "plugin_snapshot.manifest_digest",
                "SHA256:" + "1" * 64,
            ),
            ("plugin_snapshot.skill", "ha-predict"),
        ]
        for field, value in mutations:
            with self.subTest(field=field):
                request = copy.deepcopy(self.fixture["request"])
                if field == "task_kind":
                    request[field] = value
                else:
                    request["plugin_snapshot"][field.split(".")[1]] = value
                with self.assertRaises(workspace_build.ContractError):
                    workspace_build.validate_request(request)

    def test_rejects_absolute_traversal_noncanonical_and_existing_outputs(self):
        invalid_outputs = (
            "/absolute/agent",
            "../escape",
            "agents/../escape",
            "agents//agent",
            "agents\\agent",
        )
        for output in invalid_outputs:
            with self.subTest(output=output):
                request = copy.deepcopy(self.fixture["request"])
                request["draft"]["output_directory"] = output
                with self.assertRaises(workspace_build.ContractError):
                    workspace_build.validate_request(request)

        existing = self.workspace / "existing"
        existing.mkdir()
        sentinel = existing / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        request = copy.deepcopy(self.fixture["request"])
        request["draft"]["output_directory"] = "existing"
        with self.assertRaisesRegex(scaffold.SpecError, "already exists"):
            self.build(request)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_spec_digest_uses_normalized_v1_spec_not_key_order(self):
        spec = self.fixture["request"]["draft"]["spec"]
        reordered = {key: spec[key] for key in reversed(tuple(spec))}
        self.assertEqual(
            scaffold.canonical_spec_digest(scaffold.validate_spec(spec)),
            scaffold.canonical_spec_digest(scaffold.validate_spec(reordered)),
        )
        self.assertEqual(
            self.fixture["expected_result"]["draft"]["spec_digest"],
            scaffold.canonical_spec_digest(scaffold.validate_spec(spec)),
        )


if __name__ == "__main__":
    unittest.main()
