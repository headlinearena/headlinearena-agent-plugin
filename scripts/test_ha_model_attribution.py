"""Per-prediction model attribution: client gate, transport, no-silent-drop.

Mirrors the platform contract (spec: model_provider/model_name/model_version
travel top-level on every submit transport; pair-or-omit; version never alone;
snapshots per revision; unknown stays unknown). All network access is mocked.
"""
import argparse
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ha
from ha_client.errors import HAFailure
from ha_client.legacy import build_macro_civic_fallback_body
from ha_client.prediction import (
    build_civic_forecast_body,
    build_legacy_macro_body,
    build_unified_predict_body,
    model_attribution_fields,
)

registry = types.ModuleType("tools.registry")
registry.tool_result = lambda value: json.dumps(value)
registry.tool_error = lambda message: json.dumps({"error": message})
spec = importlib.util.spec_from_file_location(
    "ha_model_attribution_test_adapter", Path(__file__).resolve().parent.parent / "ha_tools.py"
)
adapter = importlib.util.module_from_spec(spec)
with mock.patch.dict(sys.modules, {"tools.registry": registry}):
    spec.loader.exec_module(adapter)

P = {"bearish": 0.2, "neutral": 0.3, "bullish": 0.5}
ACK = {"prediction_id": "p", "revision_number": 1, "model_attribution_source": "client_declared"}


class FieldValidationTests(unittest.TestCase):
    def test_nothing_declared_is_empty(self):
        self.assertEqual(model_attribution_fields(None, None, None), {})

    def test_pair_and_version_survive_verbatim_with_outer_strip(self):
        self.assertEqual(
            model_attribution_fields(" Zhipu ", " glm-4.6 ", " 20261021 "),
            {"model_provider": "Zhipu", "model_name": "glm-4.6", "model_version": "20261021"},
        )

    def test_new_model_without_version_omits_key_never_inherits(self):
        self.assertEqual(
            model_attribution_fields("OpenAI", "gpt-4o", None),
            {"model_provider": "OpenAI", "model_name": "gpt-4o"},
        )

    def test_blank_version_is_omitted_not_an_error(self):
        self.assertEqual(
            model_attribution_fields("OpenAI", "gpt-4o", "  "),
            {"model_provider": "OpenAI", "model_name": "gpt-4o"},
        )

    def test_half_pair_rejects(self):
        for triple in (("Zhipu", None, None), (None, "glm-4.6", None)):
            with self.assertRaises(HAFailure):
                model_attribution_fields(*triple)

    def test_version_alone_rejects(self):
        with self.assertRaises(HAFailure):
            model_attribution_fields(None, None, "v1")

    def test_blank_provider_or_name_rejects(self):
        for triple in (("  ", "glm-4.6", None), ("Zhipu", "  ", None)):
            with self.assertRaises(HAFailure):
                model_attribution_fields(*triple)

    def test_over_length_rejects_at_server_bounds(self):
        with self.assertRaises(HAFailure):
            model_attribution_fields("p" * 65, "n", None)
        with self.assertRaises(HAFailure):
            model_attribution_fields("p", "n" * 129, None)
        with self.assertRaises(HAFailure):
            model_attribution_fields("p", "n", "v" * 65)


class FinancialPredictTests(unittest.TestCase):
    def args(self, **kw):
        return argparse.Namespace(**dict(
            challenge_id="fixture", direction=None, confidence=None,
            probabilities=json.dumps(P), reasoning="Synthetic rationale with sufficient length",
            summary=None, revision=False, amount=None, **kw))

    def test_old_payload_has_no_model_fields(self):
        with mock.patch.object(ha, "authed", return_value=(201, dict(ACK))) as call, \
             mock.patch.object(ha, "out"), mock.patch.object(ha, "note") as noted:
            ha.cmd_predict(self.args())
        body = call.call_args.args[2]
        self.assertEqual(body, {"probabilities": P, "reasoning": body["reasoning"], "is_revision": False})
        for key in ("model_provider", "model_name", "model_version"):
            self.assertNotIn(key, body)
        noted.assert_not_called()

    def test_declared_model_reaches_body_and_acknowledged_response_is_quiet(self):
        args = self.args(model_provider="Zhipu", model_name="glm-4.6")
        with mock.patch.object(ha, "authed", return_value=(201, dict(ACK))) as call, \
             mock.patch.object(ha, "out"), mock.patch.object(ha, "note") as noted:
            ha.cmd_predict(args)
        body = call.call_args.args[2]
        self.assertEqual(body["model_provider"], "Zhipu")
        self.assertEqual(body["model_name"], "glm-4.6")
        self.assertNotIn("model_version", body)
        noted.assert_not_called()

    def test_unacknowledged_model_warns_instead_of_silent_drop(self):
        args = self.args(model_provider="Zhipu", model_name="glm-4.6")
        with mock.patch.object(ha, "authed", return_value=(201, {"prediction_id": "p"})), \
             mock.patch.object(ha, "out"), mock.patch.object(ha, "note") as noted:
            ha.cmd_predict(args)
        noted.assert_called_once()
        self.assertIn("model_attribution_source", noted.call_args.args[0])

    def test_invalid_declaration_fails_before_any_request(self):
        with mock.patch.object(ha, "authed") as call:
            with self.assertRaises(HAFailure):
                ha.cmd_predict(self.args(model_provider="Zhipu"))
        call.assert_not_called()

    def test_hold_still_carries_model_but_never_warns(self):
        args = self.args(
            forecast_context=json.dumps({"decision": "hold"}),
            expected_revision=2, model_provider="Zhipu", model_name="glm-4.6", model_version="v2",
        )
        with mock.patch.object(ha, "authed", return_value=(200, {"status": "held"})) as call, \
             mock.patch.object(ha, "out"), mock.patch.object(ha, "note") as noted:
            ha.cmd_predict(args)
        body = call.call_args.args[2]
        self.assertEqual(body["model_version"], "v2")
        noted.assert_not_called()


class UnifiedAndCivicBuilderTests(unittest.TestCase):
    def test_unified_builder_carries_model_fields(self):
        body = build_unified_predict_body(
            "c", prediction={"mean": 3.1, "std": 0.2},
            model_provider="DeepSeek", model_name="deepseek-r1", model_version="20261021",
        )
        self.assertEqual(body["model_provider"], "DeepSeek")
        self.assertEqual(body["model_name"], "deepseek-r1")
        self.assertEqual(body["model_version"], "20261021")

    def test_unified_builder_without_model_is_byte_identical_shape(self):
        body = build_unified_predict_body("c", prediction={"mean": 3.1, "std": 0.2})
        for key in ("model_provider", "model_name", "model_version"):
            self.assertNotIn(key, body)

    def test_civic_body_model_fields_do_not_change_idempotency_key(self):
        base = build_civic_forecast_body("c", {"mean": 3.1, "std": 0.2}, 25)
        declared = build_civic_forecast_body(
            "c", {"mean": 3.1, "std": 0.2}, 25,
            model_provider="Zhipu", model_name="glm-4.6",
        )
        self.assertEqual(base["idempotency_key"], declared["idempotency_key"])
        self.assertEqual(declared["model_provider"], "Zhipu")

    def test_legacy_macro_body_carries_model_fields(self):
        body = build_legacy_macro_body(
            {"mean": 3.1, "std": 0.2}, 25, model_provider="xAI", model_name="grok-4",
        )
        self.assertEqual((body["model_provider"], body["model_name"]), ("xAI", "grok-4"))
        self.assertEqual(body["predicted_value"], 3.1)

    def test_macro_civic_fallback_keeps_key_and_carries_model(self):
        base = build_macro_civic_fallback_body("c", 3.1, 0.2, 25)
        declared = build_macro_civic_fallback_body(
            "c", 3.1, 0.2, 25, model_provider="xAI", model_name="grok-4",
        )
        self.assertEqual(base["idempotency_key"], declared["idempotency_key"])
        self.assertEqual(declared["model_provider"], "xAI")


class DeprecatedAliasAndPriceEventTests(unittest.TestCase):
    def test_macro_predict_alias_body_carries_model(self):
        args = argparse.Namespace(
            challenge_id="fixture", predicted_value=3.1, predicted_std=0.2, amount=25,
            rationale=None, model_provider="Zhipu", model_name="glm-4.6", model_version=None,
        )
        with mock.patch.object(ha, "authed", return_value=(200, dict(ACK))) as call, \
             mock.patch.object(ha, "out"), mock.patch.object(ha, "note"):
            ha.cmd_macro_predict(args)
        body = call.call_args.args[2]
        self.assertEqual(body["model_provider"], "Zhipu")
        self.assertEqual(body["model_name"], "glm-4.6")

    def test_price_predict_carries_model_and_warns_when_unacknowledged(self):
        args = argparse.Namespace(
            challenge_id="price-1", mean=None, std=None, yes_probability=0.2,
            reasoning="Analysis supported by the current market evidence.",
            model_provider="Google", model_name="gemini-2.5-pro",
        )
        from ha_client.contracts import price_event_from_contract_entry
        row = price_event_from_contract_entry({
            "contract": {
                "api_contract_version": "prediction-contract-v2", "site": "global",
                "execution_family": "price_event", "submission_route": "price_event",
                "participation_contract": "score_only", "submission_atomic": True,
                "required_scopes": ["prediction:submit"], "scope_key": "BTC",
                "target_key": "BTC_PRICE_EVENTS", "outcome_shape": "binary_probability",
                "forecast_schema": {"input_encoding": "direction_confidence"},
            },
            "current_challenge": {"challenge_id": "price-1", "status": "open",
                                  "deadline": "2026-12-31", "question": {"en": "q"}},
        })
        with mock.patch.object(ha, "_fetch_price_event_challenges", return_value=[row]), \
             mock.patch.object(ha, "authed", return_value=(201, {"id": "x"})) as call, \
             mock.patch.object(ha, "out"), mock.patch.object(ha, "note") as noted:
            ha.cmd_price_predict(args)
        body = call.call_args.args[2]
        self.assertEqual(body["model_name"], "gemini-2.5-pro")
        self.assertEqual(body["probabilities"], {"bullish": 0.2, "bearish": 0.8})
        noted.assert_called_once()


class HermesAdapterTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ha, "_update_info", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_registered_schema_declares_model_fields_optionally(self):
        registered = {name: schema for name, schema, _, _ in adapter._TOOLS}
        schema = registered["ha_predict"]["parameters"]
        for key, bound in (("model_provider", 64), ("model_name", 128), ("model_version", 64)):
            self.assertIn(key, schema["properties"])
            self.assertNotIn(key, schema["required"])
            self.assertEqual(schema["properties"][key]["maxLength"], bound)
        macro = registered["ha_macro_predict"]["parameters"]
        self.assertIn("model_provider", macro["properties"])

    def test_handle_ha_predict_forwards_model_fields_to_http(self):
        with mock.patch.object(ha, "authed", return_value=(201, dict(ACK))) as call, \
             mock.patch.object(ha, "out"), mock.patch.object(ha, "note"):
            json.loads(adapter.handle_ha_predict(dict(
                challenge_id="fixture", probabilities=P,
                reasoning="A recorded market rationale",
                model_provider="Zhipu", model_name="glm-4.6", model_version="v3",
            )))
        body = call.call_args.args[2]
        self.assertEqual((body["model_provider"], body["model_name"], body["model_version"]),
                         ("Zhipu", "glm-4.6", "v3"))

    def test_handle_ha_predict_rejects_half_pair_before_any_request(self):
        with mock.patch.object(ha, "authed") as call:
            result = json.loads(adapter.handle_ha_predict(dict(
                challenge_id="fixture", probabilities=P,
                reasoning="A recorded market rationale", model_provider="Zhipu",
            )))
        self.assertIn("error", result)
        call.assert_not_called()

    def test_handle_ha_macro_predict_forwards_model_fields(self):
        with mock.patch.object(ha, "authed", return_value=(200, dict(ACK))) as call, \
             mock.patch.object(ha, "out"), mock.patch.object(ha, "note"):
            json.loads(adapter.handle_ha_macro_predict(dict(
                challenge_id="fixture", predicted_value=3.1, predicted_std=0.2, amount=25,
                model_provider="Zhipu", model_name="glm-4.6",
            )))
        body = call.call_args.args[2]
        self.assertEqual((body["model_provider"], body["model_name"]), ("Zhipu", "glm-4.6"))


if __name__ == "__main__":
    unittest.main()
