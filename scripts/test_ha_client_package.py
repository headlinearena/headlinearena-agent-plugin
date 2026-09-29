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

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ha  # noqa: E402
from ha_client import auth, contracts, errors, legacy, prediction, transport  # noqa: E402


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


class _FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = body.encode()
        self.headers = headers if headers is not None else {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TransportTest(unittest.TestCase):
    def test_normalize_origin(self):
        self.assertEqual(transport.normalize_origin("https://x.example/"), "https://x.example")
        self.assertEqual(
            transport.normalize_origin("https://x.example/api/v1"), "https://x.example"
        )
        self.assertEqual(
            transport.normalize_origin("http://localhost:9000/api/v1"), "http://localhost:9000"
        )

    def test_normalize_origin_rejects_insecure_http(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            transport.normalize_origin("http://example.com")
        self.assertEqual(
            ctx.exception.detail,
            "Insecure HA_BASE_URL 'http://example.com': "
            "HTTPS is required except for localhost",
        )

    def test_api_url(self):
        self.assertEqual(
            transport.api_url("https://x.example", "/eval/challenges"),
            "https://x.example/api/v1/eval/challenges",
        )

    def test_build_request_headers(self):
        version = {"User-Agent": "headlinearena-cli/x", "X-HA-Plugin-Version": "x",
                   "X-HA-Plugin-Host": "claude"}
        headers = transport.build_request_headers(version, token="tok", agent_id="a1",
                                                  request_id="rid")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["X-Request-Id"], "rid")
        self.assertEqual(headers["Authorization"], "Bearer tok")
        self.assertEqual(headers["X-Agent-Id"], "a1")
        self.assertEqual(headers["X-HA-Plugin-Host"], "claude")
        # Absent auth inputs must not create empty headers.
        bare = transport.build_request_headers(version)
        self.assertNotIn("Authorization", bare)
        self.assertNotIn("X-Agent-Id", bare)
        self.assertNotIn("X-Request-Id", bare)

    def test_open_json_success(self):
        with mock.patch.object(transport.urllib.request, "urlopen",
                               return_value=_FakeResponse(200, '{"ok": true}', {"A": "b"})):
            status, headers, body = transport.open_json("GET", "https://x/y")
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(headers, {"A": "b"})

    def test_open_json_empty_body_decodes_to_empty_dict(self):
        with mock.patch.object(transport.urllib.request, "urlopen",
                               return_value=_FakeResponse(204, "")):
            status, _, body = transport.open_json("POST", "https://x/y", data=b"{}")
        self.assertEqual((status, body), (204, {}))

    def test_open_json_non_json_success_uses_raw_key(self):
        with mock.patch.object(transport.urllib.request, "urlopen",
                               return_value=_FakeResponse(200, "<html>oops</html>")):
            status, _, body = transport.open_json("GET", "https://x/y")
        self.assertEqual(body, {"raw": "<html>oops</html>"})

    def test_open_json_http_error_uses_detail_key(self):
        err = urllib.error.HTTPError(
            "https://x/y", 422, "Unprocessable Entity", {}, io.BytesIO(b"not json")
        )
        with mock.patch.object(transport.urllib.request, "urlopen", side_effect=err):
            status, _, body = transport.open_json("POST", "https://x/y")
        self.assertEqual(status, 422)
        self.assertEqual(body, {"detail": "not json"})

    def test_open_json_http_error_with_json_body(self):
        err = urllib.error.HTTPError(
            "https://x/y", 403, "Forbidden", {},
            io.BytesIO(b'{"detail": "missing scope credits:stake"}'),
        )
        with mock.patch.object(transport.urllib.request, "urlopen", side_effect=err):
            status, _, body = transport.open_json("POST", "https://x/y")
        self.assertEqual((status, body), (403, {"detail": "missing scope credits:stake"}))

    def test_open_json_url_error_message_is_verbatim(self):
        err = urllib.error.URLError("connection refused")
        with mock.patch.object(transport.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(ha.HAFailure) as ctx:
                transport.open_json("GET", "https://x/y")
        self.assertEqual(ctx.exception.detail, "Cannot reach https://x/y: connection refused")

    def test_expect_alias_and_behavior(self):
        self.assertIs(ha.expect, transport.expect)
        self.assertEqual(transport.expect(200, {"detail": "x"}), {"detail": "x"})
        with self.assertRaises(ha.HAFailure) as ctx:
            transport.expect(404, {"detail": "nope"})
        self.assertEqual(ctx.exception.detail, "nope")
        self.assertEqual(ctx.exception.status, 404)

    def test_http_compositor_writes_last_response_headers(self):
        # ha.http must keep publishing the raw response headers to the module
        # global — _absorb_status_headers (passive claim sync) reads it.
        hdrs = {"X-HA-Agent-Status": "active"}
        with mock.patch.object(transport.urllib.request, "urlopen",
                               return_value=_FakeResponse(200, "{}", hdrs)):
            status, resp = ha.http("GET", "https://x/api/v1/ping")
        self.assertEqual((status, resp), (200, {}))
        self.assertEqual(ha._last_response_headers, hdrs)
        ha._last_response_headers = None


class AuthHelpersTest(unittest.TestCase):
    def test_token_is_fresh_boundary(self):
        now = 1000.0
        self.assertTrue(auth.token_is_fresh(
            {"access_token": "t", "expires_at": 1061}, now))  # 61s left > 60 margin
        self.assertFalse(auth.token_is_fresh(
            {"access_token": "t", "expires_at": 1060}, now))  # exactly margin → refresh
        self.assertFalse(auth.token_is_fresh({"access_token": "t"}, now))
        self.assertFalse(auth.token_is_fresh({}, now))
        self.assertFalse(auth.token_is_fresh(None, now))

    def test_token_request_body(self):
        self.assertEqual(
            auth.token_request_body("a1", "s3cret"),
            {"grant_type": "client_credentials", "agent_id": "a1",
             "client_secret": "s3cret"},
        )

    def test_token_from_response_expires_at(self):
        tok = auth.token_from_response(
            {"access_token": "t", "expires_in": 900}, 1000.0)
        self.assertEqual(tok, {"access_token": "t", "expires_at": 1900})
        # expires_in missing → historical 900 default
        self.assertEqual(
            auth.token_from_response({"access_token": "t"}, 100.0)["expires_at"], 1000)

    def test_inactive_account_message_all_hints(self):
        msg = auth.inactive_account_message(
            "agent not activated",
            {"claim_url": "https://ha/claim/x", "pairing_code": "4821"},
        )
        self.assertEqual(
            msg,
            "Account not active yet (agent not activated). "
            "Ask your operator to open the claim link: https://ha/claim/x "
            "(pairing code: 4821)",
        )

    def test_inactive_account_message_expired_hint(self):
        msg = auth.inactive_account_message("claim expired", {"claim_url": "https://c"})
        self.assertTrue(msg.endswith(
            " Ask your operator to open the claim link: https://c"
            " Run `ha.py claim-link` to issue a fresh claim link + pairing code."
        ))

    def test_inactive_account_message_no_entry_artifacts(self):
        self.assertEqual(
            auth.inactive_account_message("not activated", {}),
            "Account not active yet (not activated).",
        )

    def test_token_failure_message(self):
        self.assertEqual(auth.token_failure_message("boom"), "Token request failed: boom")


class LegacyCompatTest(unittest.TestCase):
    def test_cn_endpoint_detection(self):
        for url in (
            "https://headlinearena.cn",
            "https://api.headlinearena.cn/",
            "https://headlinearena.com/api/v1/cn/agent/register",
            "https://x.example/cn",           # trailing /cn without a slash
            "http://localhost:8000/cn/foo",
        ):
            self.assertTrue(legacy.is_cn_endpoint(url), url)
        for url in (
            "https://headlinearena.com",
            "https://headlinearena.com/api/v1",
            "https://xcn.example.com",        # .cn must be a host suffix, not substring
            "",
            None,
        ):
            self.assertFalse(legacy.is_cn_endpoint(url), repr(url))

    def test_missing_scope_predicate(self):
        self.assertTrue(legacy.is_missing_scope(403, {"detail": "Missing Scope: credits:stake"}))
        self.assertTrue(legacy.is_missing_scope(403, {"detail": "missing scope GC"}))
        self.assertFalse(legacy.is_missing_scope(403, {"detail": "not activated"}))
        self.assertFalse(legacy.is_missing_scope(400, {"detail": "missing scope"}))
        self.assertFalse(legacy.is_missing_scope(403, {}))

    def test_non_numeric_shape_error_predicate(self):
        self.assertTrue(legacy.is_non_numeric_shape_error(
            {"detail": "Binary forecast requires exactly yes_probability"}))
        self.assertTrue(legacy.is_non_numeric_shape_error(
            {"detail": "probabilities must sum to 1"}))
        self.assertFalse(legacy.is_non_numeric_shape_error(
            {"detail": "predicted_std must be positive"}))

    def test_missing_scope_message_is_verbatim_for_both_commands(self):
        self.assertEqual(
            legacy.missing_scope_message("macro-predict"),
            "Missing a required scope — macro-predict needs credits:stake, which is NOT granted "
            "by default. Self-grant with: `ha.py scope --add credits:stake`, then re-run.",
        )
        self.assertEqual(
            legacy.missing_scope_message("forecast"),
            "Missing a required scope — forecast needs credits:stake, which is NOT granted "
            "by default. Self-grant with: `ha.py scope --add credits:stake`, then re-run.",
        )

    def test_non_numeric_shape_message_is_verbatim(self):
        self.assertEqual(
            legacy.NON_NUMERIC_SHAPE_MESSAGE,
            "This challenge is not numeric — macro-predict only supports "
            "outcome_shape=numeric_distribution (mean/std). Use "
            "`ha.py forecast <id> ...` instead (run `ha.py challenges --track civic` "
            "to see the exact flags for this challenge_id).",
        )

    def test_quoted_scope_key(self):
        self.assertEqual(legacy.quoted_scope_key("Not subscribed to 'GC'"), "GC")
        self.assertEqual(legacy.quoted_scope_key("no quoted 'tokens_2' here"), "tokens_2")
        self.assertIsNone(legacy.quoted_scope_key("no quotes at all"))

    def test_macro_civic_fallback_uuid5_is_pinned(self):
        # The legacy 404-fallback derivation (value:std:amount) is distinct
        # from build_civic_forecast_body's (json forecast) — both are pinned
        # separately so neither can drift into the other.
        body = legacy.build_macro_civic_fallback_body("c1", 3.4, 0.15, 10)
        self.assertEqual(body["idempotency_key"], "57545182ca4752ba9c490adaaa7b3164")
        self.assertEqual(body["forecast"], {"mean": 3.4, "std": 0.15})
        self.assertEqual(body["amount"], 10)
        self.assertNotIn("rationale", body)
        with_rationale = legacy.build_macro_civic_fallback_body("ch-9", 2.0, 0.5, 25, "why")
        self.assertEqual(with_rationale["idempotency_key"], "67c725a696ff56acb43158b45ff012c5")
        self.assertEqual(with_rationale["rationale"], "why")

    def test_fallback_key_differs_from_canonical_derivation(self):
        canonical = prediction.build_civic_forecast_body(
            "c1", {"mean": 3.4, "std": 0.15}, 10)["idempotency_key"]
        fallback = legacy.build_macro_civic_fallback_body("c1", 3.4, 0.15, 10)["idempotency_key"]
        self.assertNotEqual(canonical, fallback)


if __name__ == "__main__":
    unittest.main()
