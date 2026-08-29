#!/usr/bin/env python3
"""Regression tests for Legacy Macro convergence into Civic Index.

`civic` is the canonical discovery/forecast family. The old `macro` commands
remain deprecated compatibility aliases, and numeric submissions keep routing
to the frozen legacy endpoint only while that already-open round exists.

Stdlib-only (unittest + unittest.mock). Run: python3 scripts/test_ha_civic_numeric_merge.py
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ha  # noqa: E402


class Args:
    def __init__(self, **kw):
        self.challenge_id = kw.get("challenge_id", "c1")
        self.predicted_value = kw.get("predicted_value", 3.4)
        self.predicted_std = kw.get("predicted_std", 0.15)
        self.amount = kw.get("amount", 10)
        self.rationale = kw.get("rationale", None)


class FetchCivicNumericTests(unittest.TestCase):
    def test_maps_target_key_to_a_short_asset_code(self):
        with mock.patch.object(
            ha,
            "http",
            return_value=(
                200,
                {
                    "challenges": [
                        {
                            "id": "c1",
                            "target_key": "HF_US_CPI",
                            "scope_key": "HF:HF_US_CPI",
                            "region": "US",
                            "status": "open",
                            "deadline": "2026-09-10T12:00:00Z",
                            "unit": "%",
                        }
                    ]
                },
            ),
        ):
            items = ha._fetch_civic_numeric_challenges()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["asset"], "CPI")
        self.assertEqual(items[0]["id"], "c1")

    def test_non_200_status_returns_empty_list_not_a_crash(self):
        with mock.patch.object(ha, "http", return_value=(404, {"detail": "not found"})):
            items = ha._fetch_civic_numeric_challenges()
        self.assertEqual(items, [])

    def test_asset_matches_still_works_against_merged_items(self):
        item = {"asset": "CPI", "scope_key": "HF:HF_US_CPI"}
        self.assertTrue(ha._asset_matches(item, {"CPI"}))
        self.assertFalse(ha._asset_matches(item, {"PPI"}))


class ChallengesMergeTests(unittest.TestCase):
    def test_macro_track_is_deprecated_alias_for_civic(self):
        civic_item = {"id": "c1", "asset": "CPI", "scope_key": "HF:HF_US_CPI"}
        args = mock.Mock(track="macro", asset=None, status=None, public=True)
        with (
            mock.patch.object(ha, "_fetch_civic_challenges", return_value=[civic_item]),
            mock.patch.object(ha, "creds", return_value={}),
            mock.patch.object(ha, "out") as mock_out,
        ):
            ha.cmd_challenges(args)
        payload = mock_out.call_args[0][0]
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["track"], "civic_forecast")
        self.assertIn("forecast <id>", payload["items"][0]["submit_hint"])

    def test_asset_filter_narrows_civic_items_same_as_macro_items(self):
        civic_item = {"id": "c1", "asset": "CPI", "scope_key": "HF:HF_US_CPI"}
        args = mock.Mock(track="macro", asset=["PPI"], status=None, public=True)
        with (
            mock.patch.object(ha, "_fetch_civic_challenges", return_value=[civic_item]),
            mock.patch.object(ha, "creds", return_value={}),
            mock.patch.object(ha, "out") as mock_out,
        ):
            ha.cmd_challenges(args)
        payload = mock_out.call_args[0][0]
        self.assertEqual(payload["total"], 0)


class MacroPredictRoutingTests(unittest.TestCase):
    def test_routes_to_civic_backend_only_on_404_and_translates_payload(self):
        calls = []

        def fake_authed(method, path, body=None):
            calls.append((method, path, body))
            if path == "/eval/macro/challenges/c1/predict":
                return 404, {"detail": "Challenge not found"}
            if path == "/eval/human-forecasts/challenges/c1/forecast":
                return 200, {"ok": True, "forecast_id": "f1"}
            raise AssertionError(f"unexpected path {path}")

        with (
            mock.patch.object(ha, "authed", side_effect=fake_authed),
            mock.patch.object(ha, "out") as mock_out,
        ):
            ha.cmd_macro_predict(Args())

        self.assertEqual(len(calls), 2)
        civic_body = calls[1][2]
        self.assertEqual(civic_body["forecast"], {"mean": 3.4, "std": 0.15})
        self.assertEqual(civic_body["amount"], 10)
        self.assertIn("idempotency_key", civic_body)
        mock_out.assert_called_once_with({"ok": True, "forecast_id": "f1"})

    def test_does_not_retry_on_non_404_errors(self):
        """A real validation error (e.g. 400 bad predicted_std) must surface as
        itself, never get masked by a silent retry against the other backend."""
        calls = []

        def fake_authed(method, path, body=None):
            calls.append(path)
            return 400, {"detail": "predicted_std must be positive"}

        with mock.patch.object(ha, "authed", side_effect=fake_authed):
            with self.assertRaises(ha.HAFailure):
                ha.cmd_macro_predict(Args())

        self.assertEqual(calls, ["/eval/macro/challenges/c1/predict"])

    def test_success_on_primary_backend_never_touches_civic_endpoint(self):
        calls = []

        def fake_authed(method, path, body=None):
            calls.append(path)
            return 200, {"ok": True}

        with (
            mock.patch.object(ha, "authed", side_effect=fake_authed),
            mock.patch.object(ha, "out"),
        ):
            ha.cmd_macro_predict(Args())

        self.assertEqual(calls, ["/eval/macro/challenges/c1/predict"])

    def test_missing_scope_on_civic_backend_gives_the_friendly_message(self):
        def fake_authed(method, path, body=None):
            if path == "/eval/macro/challenges/c1/predict":
                return 404, {"detail": "Challenge not found"}
            return 403, {"detail": "missing scope credits:stake"}

        with mock.patch.object(ha, "authed", side_effect=fake_authed):
            with self.assertRaises(ha.HAFailure):
                ha.cmd_macro_predict(Args())


class MacroOddsRoutingTests(unittest.TestCase):
    def test_falls_back_to_consensus_on_404(self):
        calls = []

        def fake_http(method, url, body=None, token=None, agent_id=None):
            calls.append(url)
            if "eval/macro/challenges/c1/odds" in url:
                return 404, {"detail": "not found"}
            return 200, {"consensus": "ok"}

        args = mock.Mock(challenge_id="c1")
        with mock.patch.object(ha, "http", side_effect=fake_http), mock.patch.object(
            ha, "out"
        ) as mock_out:
            ha.cmd_macro_odds(args)
        self.assertEqual(len(calls), 2)
        self.assertIn("consensus", calls[1])
        mock_out.assert_called_once_with({"consensus": "ok"})

    def test_no_fallback_when_primary_succeeds(self):
        calls = []

        def fake_http(method, url, body=None, token=None, agent_id=None):
            calls.append(url)
            return 200, {"odds": {"A": 0.5}}

        args = mock.Mock(challenge_id="c1")
        with mock.patch.object(ha, "http", side_effect=fake_http), mock.patch.object(ha, "out"):
            ha.cmd_macro_odds(args)
        self.assertEqual(len(calls), 1)


class FinancialTrackUnaffectedTests(unittest.TestCase):
    def test_financial_only_run_never_calls_civic_fetch(self):
        args = mock.Mock(track="financial", asset=None, status=None, public=True)
        with (
            mock.patch.object(ha, "_fetch_financial_challenges", return_value=[]),
            mock.patch.object(ha, "_fetch_civic_numeric_challenges") as civic_fetch,
            mock.patch.object(ha, "creds", return_value={}),
            mock.patch.object(ha, "out"),
        ):
            ha.cmd_challenges(args)
        civic_fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
