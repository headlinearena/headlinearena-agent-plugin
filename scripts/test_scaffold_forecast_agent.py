#!/usr/bin/env python3
"""Unit tests for the offline ha-build-agent scaffolder."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import scaffold_forecast_agent as scaffold


def valid_spec(outcome=None):
    return {
        "schema_version": 1,
        "name": "Gold Macro Monitor",
        "setup_mode": "guided",
        "objective": {
            "statement": "Forecast gold direction around macro releases",
            "beneficiaries": "People monitoring inflation risk",
            "decision_use": "Provide a calibrated public signal",
        },
        "forecasting_for_good": {
            "eligible": True,
            "rationale": "Gold is connected to inflation and household purchasing power",
        },
        "targets": ["GC"],
        "horizon": "24 hours",
        "outcome": outcome or {
            "type": "ternary",
            "labels": ["bullish", "neutral", "bearish"],
        },
        "data": {
            "allowed_sources": ["live challenge contract", "official releases"],
            "excluded_sources": [],
            "freshness": "recheck before forecasting",
        },
        "schedule": {
            "cadence": "hourly while open",
            "triggers": ["new official release"],
        },
        "evaluation": {
            "primary_metric": "brier_score",
            "review_cadence": "monthly",
            "minimum_resolved_forecasts": 30,
        },
        "operation": {
            "submission_policy": "human_review",
            "max_credits_per_run": 0,
        },
        "extensions": {},
    }


class ValidateSpecTests(unittest.TestCase):
    def test_supports_all_outcome_types(self):
        outcomes = [
            {"type": "ternary", "labels": ["up", "flat", "down"]},
            {
                "type": "binary",
                "labels": ["yes", "no"],
                "positive_label": "yes",
            },
            {"type": "numeric", "unit": "percent", "encoding": "samples"},
            {"type": "ordered", "labels": ["low", "medium", "high"]},
        ]
        for outcome in outcomes:
            with self.subTest(outcome=outcome["type"]):
                self.assertEqual(scaffold.validate_spec(valid_spec(outcome))["outcome"], outcome)

    def test_rejects_wrong_schema_version(self):
        for invalid in (2, True, 1.0):
            with self.subTest(schema_version=invalid):
                spec = valid_spec()
                spec["schema_version"] = invalid
                with self.assertRaisesRegex(scaffold.SpecError, "schema_version"):
                    scaffold.validate_spec(spec)

    def test_binary_requires_matching_positive_label(self):
        for positive_label in (None, "YES", "maybe"):
            with self.subTest(positive_label=positive_label):
                outcome = {
                    "type": "binary",
                    "labels": ["yes", "no"],
                    "positive_label": positive_label,
                }
                with self.assertRaisesRegex(scaffold.SpecError, "positive_label"):
                    scaffold.validate_spec(valid_spec(outcome))

    def test_binary_instructions_render_positive_label(self):
        spec = scaffold.validate_spec(
            valid_spec(
                {
                    "type": "binary",
                    "labels": ["met", "not_met"],
                    "positive_label": "met",
                }
            )
        )
        self.assertIn(
            "Outcome: binary: met, not_met (positive: met)",
            scaffold.render_instructions(spec),
        )

    def test_rejects_false_reviewed_eligibility_attestation(self):
        spec = valid_spec()
        spec["forecasting_for_good"]["eligible"] = False
        with self.assertRaisesRegex(scaffold.SpecError, "eligible must be true"):
            scaffold.validate_spec(spec)

    def test_rejects_unknown_top_level_fields(self):
        spec = valid_spec()
        spec["provider_magic"] = True
        with self.assertRaisesRegex(scaffold.SpecError, "extensions"):
            scaffold.validate_spec(spec)

    def test_rejects_unknown_nested_fields(self):
        spec = valid_spec()
        spec["data"]["provider_magic"] = True
        with self.assertRaisesRegex(scaffold.SpecError, "extensions"):
            scaffold.validate_spec(spec)

    def test_rejects_non_finite_credit_limit(self):
        spec = valid_spec()
        spec["operation"]["max_credits_per_run"] = float("nan")
        with self.assertRaisesRegex(scaffold.SpecError, "positive number"):
            scaffold.validate_spec(spec)


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / "workspace"
        self.workspace.mkdir()
        self.spec_file = Path(self.temp.name) / "input.json"
        self.spec_file.write_text(json.dumps(valid_spec()), encoding="utf-8")

    def test_generates_auditable_files_without_network(self):
        result = scaffold.scaffold(
            self.spec_file,
            self.workspace,
            None,
            created_at="2026-09-22T12:00:00Z",
        )
        output = self.workspace / "forecast-agents" / "gold-macro-monitor"
        self.assertEqual(result["network_calls"], 0)
        generated = json.loads((output / "forecast-agent.json").read_text(encoding="utf-8"))
        self.assertEqual(generated["schema_version"], 1)
        self.assertEqual(generated["audit"]["builder_version"], "1.35.0")
        self.assertEqual(generated["audit"]["created_at"], "2026-09-22T12:00:00Z")
        instructions = (output / "AGENT.md").read_text(encoding="utf-8")
        self.assertIn("not registered, deployed, scheduled, or running", instructions)
        self.assertIn("stop with a clear user-facing message if required credit is unavailable", instructions)

    def test_refuses_existing_output_by_default(self):
        output = self.workspace / "existing"
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(scaffold.SpecError, "already exists"):
            scaffold.scaffold(self.spec_file, self.workspace, "existing")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_second_file_failure_removes_entire_scaffold(self):
        real_write = scaffold._write_new_file
        write_count = 0

        def fail_second_write(directory_fd, target_name, content):
            nonlocal write_count
            write_count += 1
            if write_count == 2:
                raise OSError("simulated second file failure")
            return real_write(directory_fd, target_name, content)

        with mock.patch.object(
            scaffold, "_write_new_file", side_effect=fail_second_write
        ):
            with self.assertRaisesRegex(OSError, "second file failure"):
                scaffold.scaffold(
                    self.spec_file,
                    self.workspace,
                    "new-parent/partial",
                    created_at="2026-09-22T12:00:00Z",
                )

        self.assertFalse((self.workspace / "new-parent").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_ancestor_swap_never_writes_through_outside_symlink(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        real_create_files = scaffold._create_files

        def swap_ancestor_then_write(directory_fd, files):
            parent = self.workspace / "parent"
            moved_parent = self.workspace / "parent-moved"
            parent.rename(moved_parent)
            parent.symlink_to(outside, target_is_directory=True)
            return real_create_files(directory_fd, files)

        with mock.patch.object(
            scaffold,
            "_create_files",
            side_effect=swap_ancestor_then_write,
        ):
            with self.assertRaisesRegex(
                scaffold.SpecError, "output ancestry changed"
            ):
                scaffold.scaffold(
                    self.spec_file,
                    self.workspace,
                    "parent/race",
                    created_at="2026-09-22T12:00:00Z",
                )

        self.assertEqual(list(outside.iterdir()), [])
        moved = self.workspace / "parent-moved" / "race"
        self.assertFalse(moved.exists())

    def test_fails_closed_without_secure_dirfd_support(self):
        output = self.workspace / "unsupported"
        with mock.patch.object(
            scaffold, "_supports_secure_dirfd", return_value=False
        ):
            with self.assertRaisesRegex(
                scaffold.SpecError, "secure directory-relative writes"
            ):
                scaffold.scaffold(
                    self.spec_file,
                    self.workspace,
                    "unsupported",
                    created_at="2026-09-22T12:00:00Z",
                )
        self.assertFalse(output.exists())

    def test_rejects_path_traversal_and_absolute_output(self):
        for output in ("../escape", str(Path(self.temp.name) / "absolute")):
            with self.subTest(output=output):
                with self.assertRaisesRegex(scaffold.SpecError, "relative path"):
                    scaffold.scaffold(self.spec_file, self.workspace, output)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_rejects_symlink_escape(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.workspace / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(scaffold.SpecError, "safe directory"):
            scaffold.scaffold(self.spec_file, self.workspace, "link/agent")

if __name__ == "__main__":
    unittest.main()
