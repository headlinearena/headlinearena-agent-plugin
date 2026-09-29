"""Tests for the ha_client package (Phase 2 Step 1 extraction).

Two goals:
1. The extraction is invisible: ha.py re-binds every moved function to its
   historical underscore name as the *same object*, so `mock.patch.object(ha,
   ...)` in the older suites and the Hermes adapter's `except ha.HAFailure`
   keep resolving to the package's definitions.
2. Behavior (and byte-identical error messages) survived the move — including
   the uuid5 idempotency-key derivation, which is pinned to hardcoded digests
   so any drift in the derivation string fails loudly: the server dedups
   predictions on that key.
"""

import json
import os
import sys
import tempfile
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ha  # noqa: E402
from ha_client import contracts, errors, prediction  # noqa: E402


class AliasIdentityTest(unittest.TestCase):
    """ha.<historical name> must BE the ha_client object, not a copy."""

    def test_failures_and_note_are_shared(self):
        self.assertIs(ha.HAFailure, errors.HAFailure)
        self.assertIs(ha.fail, errors.fail)
        self.assertIs(ha.note, errors.note)

    def test_prediction_helpers_are_shared(self):
        self.assertIs(ha._macro_predict_body, prediction.macro_predict_body)
        self.assertIs(ha._reject_client_bin, prediction.reject_client_bin)
        self.assertIs(ha._parse_samples, prediction.parse_samples)
        self.assertIs(ha._build_forecast_payload, prediction.build_forecast_payload)

    def test_patch_object_still_intercepts(self):
        # The mechanism the older suites rely on: patching ha's name must
        # divert calls made inside ha's own command functions.
        with mock.patch.object(ha, "note") as m:
            ha.note("x")
            m.assert_called_once_with("x")


class ErrorsTest(unittest.TestCase):
    def test_fail_raises_hafailure_with_detail_and_status(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            errors.fail("boom", 403)
        self.assertEqual(ctx.exception.detail, "boom")
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(str(ctx.exception), "boom")


class TernaryVectorTest(unittest.TestCase):
    def test_valid_string_is_parsed_and_coerced(self):
        raw = '{"bearish": 0.6, "neutral": 0.35, "bullish": 0.05}'
        result = ha.validate_ternary_vector(ha.parse_probabilities_arg(raw))
        self.assertEqual(result, {"bearish": 0.6, "neutral": 0.35, "bullish": 0.05})
        self.assertTrue(all(isinstance(v, float) for v in result.values()))

    def test_dict_passes_through_unchanged(self):
        self.assertIsNone(ha.parse_probabilities_arg(None))
        d = {"bearish": 1, "neutral": 0, "bullish": 0}
        self.assertEqual(ha.parse_probabilities_arg(d), d)

    def test_bad_json_message_is_verbatim(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha.parse_probabilities_arg("not json")
        self.assertEqual(
            ctx.exception.detail,
            '--probabilities must be a JSON object, e.g. '
            '\'{"bearish": 0.60, "neutral": 0.35, "bullish": 0.05}\'',
        )

    def test_wrong_keys_message_is_verbatim(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha.validate_ternary_vector({"bullish": 1.0})
        self.assertEqual(
            ctx.exception.detail,
            "probabilities must be an object with exactly the keys bearish, neutral, bullish",
        )

    def test_non_numeric_message_is_verbatim(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha.validate_ternary_vector(
                {"bearish": "high", "neutral": 0.0, "bullish": 0.0}
            )
        self.assertEqual(ctx.exception.detail, "probabilities values must be numbers")

    def test_sum_message_is_verbatim(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha.validate_ternary_vector(
                {"bearish": 0.5, "neutral": 0.5, "bullish": 0.5}
            )
        self.assertEqual(
            ctx.exception.detail,
            "probabilities must be non-negative and sum to 1 (tolerance 1e-6)",
        )

    def test_negative_value_rejected(self):
        with self.assertRaises(ha.HAFailure):
            ha.validate_ternary_vector(
                {"bearish": -0.1, "neutral": 1.0, "bullish": 0.1}
            )


class ParseSamplesTest(unittest.TestCase):
    def test_inline_comma_separated(self):
        vals = ha._parse_samples("1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5, 10.5")
        self.assertEqual(len(vals), 10)
        self.assertEqual(vals[0], 1.5)
        self.assertEqual(vals[-1], 10.5)

    def test_at_file_json_array(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump([0.5] * 12, fh)
            path = fh.name
        try:
            self.assertEqual(ha._parse_samples(f"@{path}"), [0.5] * 12)
        finally:
            os.unlink(path)

    def test_too_few_message_is_verbatim(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha._parse_samples("1, 2, 3")
        self.assertEqual(
            ctx.exception.detail, "--samples needs between 10 and 1000 values, got 3"
        )

    def test_unreadable_file_message_is_verbatim(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha._parse_samples("@/nonexistent/samples.json")
        self.assertIn("could not be read", ctx.exception.detail)

    def test_non_numeric_message_is_verbatim(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha._parse_samples("a,b,c,d,e,f,g,h,i,j")
        self.assertEqual(
            ctx.exception.detail, "--samples entries must be numbers, got 'a'"
        )


class ForecastPayloadTest(unittest.TestCase):
    def _args(self, **kw):
        defaults = dict(samples=None, mean=None, std=None, yes_probability=None,
                        probability=None, bin=None, bin_label=None)
        defaults.update(kw)
        return mock.Mock(**defaults)

    def test_numeric_mean_std(self):
        payload = ha._build_forecast_payload(
            "numeric_distribution",
            self._args(mean=2.5, std=0.3),
            {"forecast_schema": None},
        )
        self.assertEqual(payload, {"mean": 2.5, "std": 0.3})

    def test_numeric_missing_mean_message_is_verbatim(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha._build_forecast_payload(
                "numeric_distribution",
                self._args(),
                {"forecast_schema": {"unit": "pct"}},
            )
        self.assertEqual(
            ctx.exception.detail,
            "This challenge is numeric_distribution — pass --mean and --std, "
            "or --samples with your raw predictive samples (schema: {'unit': 'pct'})",
        )

    def test_std_must_be_positive(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha._build_forecast_payload(
                "numeric_distribution",
                self._args(mean=1.0, std=0.0),
                {"forecast_schema": None},
            )
        self.assertEqual(ctx.exception.detail, "--std must be > 0")

    def test_binary_out_of_range(self):
        with self.assertRaises(ha.HAFailure):
            ha._build_forecast_payload(
                "binary_probability",
                self._args(yes_probability=1.5),
                {"forecast_schema": None},
            )

    def test_ordered_categorical_full_vector(self):
        challenge = {"forecast_schema": {"categories": [
            {"key": "hold"}, {"key": "hike"}, {"key": "cut"},
        ]}}
        payload = ha._build_forecast_payload(
            "ordered_categorical_distribution",
            self._args(probability=["hold=0.6", "hike=0.2", "cut=0.2"]),
            challenge,
        )
        self.assertEqual(
            payload, {"probabilities": {"hold": 0.6, "hike": 0.2, "cut": 0.2}}
        )

    def test_ordered_categorical_wrong_categories(self):
        challenge = {"forecast_schema": {"categories": [{"key": "hold"}, {"key": "hike"}]}}
        with self.assertRaises(ha.HAFailure) as ctx:
            ha._build_forecast_payload(
                "ordered_categorical_distribution",
                self._args(probability=["hold=0.5", "cut=0.5"]),
                challenge,
            )
        self.assertEqual(
            ctx.exception.detail,
            "--probability categories ['cut', 'hold'] do not match the frozen set ['hold', 'hike']",
        )

    def test_unknown_shape_message_is_verbatim(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha._build_forecast_payload("quantum_superposition", self._args(), {})
        self.assertEqual(
            ctx.exception.detail,
            "Unrecognized outcome_shape 'quantum_superposition' for this challenge — "
            "this plugin version may be older than the backend's contract. "
            "Run `ha.py update-check`.",
        )

    def test_client_bin_rejected_message_is_verbatim(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha._reject_client_bin(self._args(bin_label="3.0-3.5"))
        self.assertTrue(ctx.exception.detail.startswith(
            "The server maps your forecast statistic to exactly one frozen bin itself"
        ))


class BodyBuildersTest(unittest.TestCase):
    def test_legacy_macro_body(self):
        self.assertEqual(
            prediction.build_legacy_macro_body({"mean": 2.5, "std": 0.3}, 10),
            {"predicted_value": 2.5, "predicted_std": 0.3, "amount": 10},
        )

    def test_legacy_macro_body_with_rationale(self):
        body = prediction.build_legacy_macro_body({"mean": 2.5, "std": 0.3}, 10, "because")
        self.assertEqual(body["rationale"], "because")

    def test_macro_predict_body_passthrough(self):
        args = mock.Mock(predicted_value=1.0, predicted_std=0.2, amount=5, rationale=None)
        self.assertEqual(
            ha._macro_predict_body(args),
            {"predicted_value": 1.0, "predicted_std": 0.2, "amount": 5},
        )

    def test_civic_uuid5_key_is_pinned(self):
        # Regression guard: these digests are the historical derivation. The
        # server dedups on idempotency_key, so a drifted derivation string
        # would silently double-submit instead of deduping.
        cases = [
            ({"mean": 2.5, "std": 0.3}, 10, "d0324372f438578ca7a7f9360ff169f6"),
            ({"samples": [0.1] * 10}, 5, "fccb53374d9155b4b7171337c413b0f0"),
            ({"yes_probability": 0.7}, 3, "10de813375965a17b425b88059d1b0af"),
        ]
        for forecast, amount, expected in cases:
            body = prediction.build_civic_forecast_body("cpi-2026-09", forecast, amount)
            self.assertEqual(body["idempotency_key"], expected)
            self.assertEqual(body["forecast"], forecast)
            self.assertEqual(body["amount"], amount)
            self.assertNotIn("rationale", body)
            self.assertNotIn("expected_revision", body)

    def test_civic_explicit_key_and_optional_fields(self):
        body = prediction.build_civic_forecast_body(
            "cpi-2026-09", {"mean": 2.5, "std": 0.3}, 10,
            idempotency_key="agent-supplied", rationale="why",
            expected_revision=2,
        )
        self.assertEqual(body["idempotency_key"], "agent-supplied")
        self.assertEqual(body["rationale"], "why")
        self.assertEqual(body["expected_revision"], 2)

    def test_historical_uuid5_derivation_string(self):
        # The formula itself, spelled out once more so a change to either
        # side (builder or derivation contract) is caught.
        forecast = {"mean": 2.5, "std": 0.3}
        self.assertEqual(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"cpi-2026-09:{json.dumps(forecast, sort_keys=True)}:10",
            ).hex,
            "d0324372f438578ca7a7f9360ff169f6",
        )


class ContractsAliasTest(unittest.TestCase):
    def test_projections_and_hint_table_are_shared(self):
        self.assertIs(ha._civic_from_contract_entry, contracts.civic_from_contract_entry)
        self.assertIs(ha._legacy_macro_as_civic, contracts.legacy_macro_as_civic)
        self.assertIs(ha._civic_asset_from_target_key, contracts.civic_asset_from_target_key)
        self.assertIs(ha._FORECAST_SUBMIT_HINT, contracts.FORECAST_SUBMIT_HINTS)

    def test_hint_keys_are_the_submittable_shapes(self):
        # The key set doubles as the accepted outcome_shape enum in
        # civic_from_contract_entry — pin it so a new shape cannot silently
        # widen discovery without a deliberate hint being added.
        self.assertEqual(
            set(contracts.FORECAST_SUBMIT_HINTS),
            {
                "numeric_distribution",
                "binary_probability",
                "ordered_categorical_distribution",
            },
        )
        for hint in contracts.FORECAST_SUBMIT_HINTS.values():
            self.assertTrue(hint.startswith("forecast <id>"))


class ContractParsingTest(unittest.TestCase):
    def _response(self, version="prediction-contract-v2", entries=None, **extra):
        body = {"api_contract_version": version, "entries": entries or []}
        body.update(extra)
        return body

    def test_valid_response_returns_entries(self):
        entries = [{"contract": {}}, {"contract": {}}]
        self.assertEqual(
            ha.parse_contract_response(self._response(entries=entries)), entries
        )

    def test_unknown_version_message_is_verbatim(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha.parse_contract_response(self._response(version="prediction-contract-v3"))
        self.assertEqual(
            ctx.exception.detail,
            "Unsupported prediction discovery contract; expected prediction-contract-v2. "
            "Update the HeadlineArena plugin before submitting.",
        )

    def test_non_dict_response_fails_closed(self):
        with self.assertRaises(ha.HAFailure):
            ha.parse_contract_response(["not", "a", "dict"])

    def test_malformed_entries_message_is_verbatim(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha.parse_contract_response(
                self._response(entries={"not": "a list"})
            )
        self.assertEqual(
            ctx.exception.detail,
            "Malformed prediction-contract-v2 response: entries must be a list",
        )

    def test_asset_derivation(self):
        self.assertEqual(ha._civic_asset_from_target_key("HF_US_CPI"), "CPI")
        self.assertEqual(ha._civic_asset_from_target_key("HF_US_JOBLESS_CLAIMS"),
                         "JOBLESS_CLAIMS")
        self.assertEqual(ha._civic_asset_from_target_key("CPI"), "CPI")
        self.assertEqual(ha._civic_asset_from_target_key(""), "")
        self.assertEqual(ha._civic_asset_from_target_key(None), "")

    def test_non_dict_entry_projected_to_none(self):
        self.assertIsNone(ha._civic_from_contract_entry("nope"))
        self.assertIsNone(ha._civic_from_contract_entry({}))
        self.assertIsNone(ha._legacy_macro_as_civic({"not": "an id"}))

    def test_legacy_macro_projection_carries_frozen_route(self):
        projected = ha._legacy_macro_as_civic(
            {"id": "m-1", "asset": "CPI", "deadline": "2026-10-01", "unit": "pct"}
        )
        self.assertEqual(projected["submission_route"], "macro_numeric_legacy")
        self.assertEqual(projected["outcome_shape"], "numeric_distribution")
        self.assertEqual(projected["compatibility_status"], "legacy_open_round")
        self.assertEqual(projected["forecast_schema"]["required_fields"], ["mean", "std"])
        self.assertEqual(projected["asset"], "CPI")

    def test_legacy_human_forecast_fallback_projection(self):
        projected = ha._civic_from_legacy_human_forecast(
            {"id": "hf-1", "target_key": "HF_US_CPI", "outcome_shape": "binary_probability"}
        )
        self.assertEqual(projected["asset"], "CPI")
        self.assertEqual(projected["submission_route"], "human_forecast")
        self.assertEqual(projected["scope_key"], "HF_US_CPI")
        self.assertEqual(projected["outcome_shape"], "binary_probability")


if __name__ == "__main__":
    unittest.main()
