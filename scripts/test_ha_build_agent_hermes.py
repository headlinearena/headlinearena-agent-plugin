#!/usr/bin/env python3
"""Tests for Hermes' native offline forecasting-agent scaffold tool."""

import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


def _load_ha_tools():
    registry = types.ModuleType("tools.registry")
    registry.tool_result = lambda payload: {"kind": "result", "payload": payload}
    registry.tool_error = lambda message: {"kind": "error", "message": message}
    tools = types.ModuleType("tools")
    tools.registry = registry
    sys.modules["tools"] = tools
    sys.modules["tools.registry"] = registry
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    sys.modules.pop("ha_tools", None)
    return importlib.import_module("ha_tools")


def approved_spec():
    return {
        "schema_version": 1,
        "name": "Binary Policy Monitor",
        "setup_mode": "guided",
        "objective": {
            "statement": "Forecast whether a public policy target will be met",
            "beneficiaries": "People planning around the policy outcome",
            "decision_use": "Provide a calibrated public signal",
        },
        "forecasting_for_good": {
            "eligible": True,
            "rationale": "The policy outcome affects household decisions",
        },
        "targets": ["policy_target"],
        "horizon": "by the official resolution date",
        "outcome": {
            "type": "binary",
            "labels": ["met", "not_met"],
            "positive_label": "met",
        },
        "data": {
            "allowed_sources": ["official publication"],
            "excluded_sources": [],
            "freshness": "check before each forecast",
        },
        "schedule": {"cadence": "daily", "triggers": ["official update"]},
        "evaluation": {
            "primary_metric": "brier_score",
            "review_cadence": "monthly",
            "minimum_resolved_forecasts": 20,
        },
    }


class HermesBuildAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = _load_ha_tools()

    def test_registry_and_manifest_name_match(self):
        names = [entry[0] for entry in self.tools._TOOLS]
        self.assertIn("ha_build_agent", names)
        self.assertEqual(
            self.tools.HA_BUILD_AGENT_SCHEMA["name"], "ha_build_agent"
        )
        self.assertNotIn(
            "force",
            self.tools.HA_BUILD_AGENT_SCHEMA["parameters"]["properties"],
        )
        manifest = (
            Path(__file__).resolve().parent.parent / "plugin.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("  - ha_build_agent\n", manifest)

    def test_handler_scaffolds_without_update_or_api_call(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                self.tools.ha,
                "_update_info",
                side_effect=AssertionError("offline tool must not check network"),
            ):
                result = self.tools.handle_ha_build_agent(
                    {
                        "spec": approved_spec(),
                        "workspace": directory,
                        "output": "agents/policy-monitor",
                    }
                )
            self.assertEqual(result["kind"], "result")
            self.assertEqual(result["payload"]["network_calls"], 0)
            generated = json.loads(
                (Path(directory) / "agents/policy-monitor/forecast-agent.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(generated["outcome"]["positive_label"], "met")

    def test_invalid_attestation_returns_tool_error(self):
        spec = approved_spec()
        spec["forecasting_for_good"]["eligible"] = False
        result = self.tools.handle_ha_build_agent({"spec": spec})
        self.assertEqual(result["kind"], "error")
        self.assertIn("eligible must be true", result["message"])


if __name__ == "__main__":
    unittest.main()
