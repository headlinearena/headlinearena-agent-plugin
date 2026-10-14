"""Hermes host adapter contract: context survives the CLI wrapper and readback."""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha

registry = types.ModuleType("tools.registry")
registry.tool_result = lambda value: json.dumps(value)
registry.tool_error = lambda message: json.dumps({"error": message})
spec = importlib.util.spec_from_file_location(
    "ha_hermes_context_test_adapter", Path(__file__).resolve().parent.parent / "ha_tools.py"
)
adapter = importlib.util.module_from_spec(spec)
with mock.patch.dict(sys.modules, {"tools.registry": registry}):
    spec.loader.exec_module(adapter)

P = {"bearish": 0.2, "neutral": 0.3, "bullish": 0.5}


class HermesContextTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ha, "_update_info", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def predict(self, **extra):
        return json.loads(adapter.handle_ha_predict(dict(
            challenge_id="fixture", probabilities=P, reasoning="A recorded market rationale", **extra
        )))

    def test_context_visible_and_optional_in_registered_schema(self):
        registered = {name: schema for name, schema, _, _ in adapter._TOOLS}
        schema = registered["ha_predict"]["parameters"]
        self.assertIn("forecast_context", schema["properties"])
        self.assertNotIn("forecast_context", schema["required"])
        refs = schema["properties"]["forecast_context"]["properties"]["evidence_refs"]
        self.assertEqual(refs["maxItems"], 32)
        self.assertIn("ha_forecast_contexts", registered)

    def test_metadata_and_revision_controls_reach_http_unchanged(self):
        context = {"run_ref": "existing-run", "observed_at": "2026-10-14T04:15:00Z",
                   "evidence_refs": [{"kind": "external", "namespace": "own-store", "id": "123"}]}
        with mock.patch.object(ha, "authed", return_value=(201, {"prediction_id": "p", "revision_number": 2,
                                                               "context_receipt_id": "r", "context_status": "pending_verification"})) as call:
            result = self.predict(forecast_context=context, expected_revision=1,
                                  idempotency_key="stable-key", revision_reason="New release", trigger_event_id="event")
        body = call.call_args.args[2]
        self.assertEqual(body["forecast_context"], context)
        self.assertEqual(body["idempotency_key"], "stable-key")
        self.assertEqual(body["expected_revision"], 1)
        self.assertEqual(body["revision_reason"], "New release")
        self.assertEqual(body["trigger_event_id"], "event")
        self.assertEqual(result["context_receipt_id"], "r")
        self.assertEqual(result["revision_number"], 2)
        self.assertIn("client_declared", result["_forecast_context"])
        call.assert_called_once()

    def test_old_prediction_body_is_unchanged(self):
        with mock.patch.object(ha, "authed", return_value=(201, {})) as call:
            self.predict()
        self.assertEqual(call.call_args.args[2], {
            "probabilities": P, "reasoning": "A recorded market rationale", "is_revision": False})

    def test_abstain_can_omit_reasoning_and_vector(self):
        with mock.patch.object(ha, "authed", return_value=(200, {"status": "abstained"})) as call:
            result = json.loads(adapter.handle_ha_predict({"challenge_id": "fixture",
                "forecast_context": {"decision": "abstain"}, "expected_revision": 2}))
        self.assertEqual(result["status"], "abstained")
        self.assertNotIn("probabilities", call.call_args.args[2])
        self.assertNotIn("reasoning", call.call_args.args[2])

    def test_ordinary_prediction_still_requires_reasoning(self):
        with mock.patch.object(ha, "authed") as call:
            result = json.loads(adapter.handle_ha_predict({"challenge_id": "fixture", "probabilities": P}))
        self.assertIn("reasoning", result["error"])
        call.assert_not_called()

    def test_hold_requires_revision_before_any_request(self):
        with mock.patch.object(ha, "authed") as call:
            result = self.predict(forecast_context={"decision": "hold"})
        self.assertIn("expected-revision", result["error"])
        call.assert_not_called()

    def test_hold_transmits_complete_vector_without_public_revision(self):
        with mock.patch.object(ha, "authed", return_value=(200, {"status": "held"})) as call:
            result = self.predict(forecast_context={"decision": "hold"}, expected_revision=2)
        self.assertEqual(result["status"], "held")
        self.assertEqual(call.call_args.args[2]["probabilities"], P)
        self.assertFalse(call.call_args.args[2]["is_revision"])
        self.assertNotIn("amount", call.call_args.args[2])

    def test_context_failure_keeps_success_and_does_not_resubmit(self):
        with mock.patch.object(ha, "authed", return_value=(201, {
                "prediction_id": "p", "context_status": "storage_failed"})) as call:
            result = self.predict(forecast_context={"run_ref": "existing-run"})
        self.assertEqual(result["prediction_id"], "p")
        self.assertIn("do not resubmit", result["_forecast_context"])
        call.assert_called_once()

    def test_unsupported_context_fails_without_network_or_echo(self):
        with mock.patch.object(ha, "authed") as call:
            result = self.predict(forecast_context={"full_prompt": "private-fixture-never-echo"})
        self.assertIn("error", result)
        self.assertNotIn("private-fixture-never-echo", result["error"])
        call.assert_not_called()

    def test_private_readback_uses_authenticated_get_and_cursor(self):
        with mock.patch.object(ha, "authed", return_value=(200, {"receipts": [{"context_receipt_id": "r"}]})) as call:
            result = json.loads(adapter.handle_ha_forecast_contexts({"challenge_id": "fixture",
                "revision_number": 2, "limit": 5, "cursor": "page-token"}))
        self.assertEqual(result["receipts"][0]["context_receipt_id"], "r")
        self.assertEqual(call.call_args.args, ("GET", "/eval/challenges/fixture/forecast-contexts?limit=5&revision_number=2&cursor=page-token"))

    def test_paper_trade_warning_survives_context_guidance(self):
        with mock.patch.object(ha, "authed", return_value=(201, {"counts_for_score": False})):
            result = self.predict()
        self.assertIn("paper-trade", result["_paper_trade"])
        self.assertIn("_forecast_context", result)


if __name__ == "__main__":
    unittest.main()
