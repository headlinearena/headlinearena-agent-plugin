#!/usr/bin/env python3
"""Tests for the 1.31.0 unified-forecast-contract additions (Agent A9 of
docs/superpowers/plans/2026-08-26-unified-predictions-launch.md in the
public_events repo, §6 "HA Plugin release decision").

Covers:
- `_fetch_civic_challenges` (full-fidelity discovery: outcome_shape,
  forecast_schema, bins, target_key kept — unlike the legacy
  `_fetch_civic_numeric_challenges`, which now ALSO filters to
  numeric_distribution-only, a real behavior change from 1.30.0 covered
  here and reconciled against test_ha_civic_numeric_merge.py).
- `challenges --track civic` (new, opt-in; `--track all/financial/macro`
  unchanged — see test_ha_civic_numeric_merge.py for those).
- `forecast` (new command): payload construction per outcome_shape,
  --bin/--bin-label rejection, ordered-category validation.
- `macro-predict`'s new shape-mismatch redirect hint.

Stdlib-only (unittest + unittest.mock). Run:
  python3 scripts/test_ha_forecast_contract.py
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ha  # noqa: E402


NUMERIC_ITEM = {
    "id": "c-numeric",
    "target_key": "HF_US_CPI",
    "scope_key": "HF:HF_US_CPI",
    "region": "US",
    "status": "open",
    "deadline": "2026-09-10T12:00:00Z",
    "unit": "%",
    "outcome_shape": "numeric_distribution",
    "forecast_schema": {"mean": "finite number", "std": "positive finite number"},
    "bins": [{"label": "A", "lo": None, "hi": 2.0}],
}
BINARY_ITEM = {
    "id": "c-binary",
    "target_key": "HF_CN_LPR",
    "scope_key": "HF:HF_CN_LPR",
    "region": "CN",
    "status": "open",
    "deadline": "2026-09-20T01:00:00Z",
    "unit": None,
    "outcome_shape": "binary_probability",
    "forecast_schema": {"yes_probability": "number in [0,1]"},
    "bins": [{"label": "YES", "category": "yes"}, {"label": "NO", "category": "no"}],
}
ORDERED_ITEM = {
    "id": "c-ordered",
    "target_key": "HF_US_ICLAIMS",
    "scope_key": "HF:HF_US_ICLAIMS",
    "region": "US",
    "status": "open",
    "deadline": "2026-09-05T12:30:00Z",
    "unit": None,
    "outcome_shape": "ordered_categorical_distribution",
    "forecast_schema": {"probabilities": ["down", "flat", "up"]},
    "bins": [{"label": "Down", "category": "down"}, {"label": "Flat", "category": "flat"},
             {"label": "Up", "category": "up"}],
}


class FetchCivicChallengesTests(unittest.TestCase):
    def test_keeps_full_fidelity_fields(self):
        with mock.patch.object(ha, "http", return_value=(200, {"challenges": [BINARY_ITEM]})):
            items = ha._fetch_civic_challenges()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["outcome_shape"], "binary_probability")
        self.assertEqual(item["forecast_schema"], BINARY_ITEM["forecast_schema"])
        self.assertEqual(item["bins"], BINARY_ITEM["bins"])
        self.assertEqual(item["target_key"], "HF_CN_LPR")
        self.assertEqual(item["asset"], "LPR")  # "HF_CN_LPR" -> drop family+region, same as legacy behavior

    def test_includes_all_shapes_unfiltered(self):
        with mock.patch.object(
            ha, "http", return_value=(200, {"challenges": [NUMERIC_ITEM, BINARY_ITEM, ORDERED_ITEM]})
        ):
            items = ha._fetch_civic_challenges()
        self.assertEqual(len(items), 3)

    def test_non_200_status_returns_empty_list(self):
        with mock.patch.object(ha, "http", return_value=(500, {"detail": "error"})):
            items = ha._fetch_civic_challenges()
        self.assertEqual(items, [])


class LegacyCivicNumericNowFiltersShapeTests(unittest.TestCase):
    """New behavior vs 1.30.0: binary/ordered items are dropped from the
    legacy macro-coerced list — they would otherwise carry a submit_hint
    (`macro-predict --predicted-value/--predicted-std`) that 400s at
    submit time for these shapes."""

    def test_numeric_item_still_included(self):
        with mock.patch.object(ha, "http", return_value=(200, {"challenges": [NUMERIC_ITEM]})):
            items = ha._fetch_civic_numeric_challenges()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "c-numeric")

    def test_binary_item_excluded(self):
        with mock.patch.object(ha, "http", return_value=(200, {"challenges": [BINARY_ITEM]})):
            items = ha._fetch_civic_numeric_challenges()
        self.assertEqual(items, [])

    def test_ordered_item_excluded(self):
        with mock.patch.object(ha, "http", return_value=(200, {"challenges": [ORDERED_ITEM]})):
            items = ha._fetch_civic_numeric_challenges()
        self.assertEqual(items, [])

    def test_mixed_list_keeps_only_numeric(self):
        with mock.patch.object(
            ha, "http", return_value=(200, {"challenges": [NUMERIC_ITEM, BINARY_ITEM, ORDERED_ITEM]})
        ):
            items = ha._fetch_civic_numeric_challenges()
        self.assertEqual([i["id"] for i in items], ["c-numeric"])

    def test_item_missing_outcome_shape_key_still_included(self):
        """Defensive default: an older backend response without the field at
        all must not silently disappear from discovery."""
        legacy_shaped_item = dict(NUMERIC_ITEM)
        del legacy_shaped_item["outcome_shape"]
        with mock.patch.object(ha, "http", return_value=(200, {"challenges": [legacy_shaped_item]})):
            items = ha._fetch_civic_numeric_challenges()
        self.assertEqual(len(items), 1)


class ChallengesCivicTrackTests(unittest.TestCase):
    def test_civic_track_tags_items_and_gives_shape_specific_submit_hint(self):
        args = mock.Mock(track="civic", asset=None, status="open", public=True)
        with (
            mock.patch.object(ha, "_fetch_civic_challenges", return_value=[
                dict(NUMERIC_ITEM, asset="CPI"), dict(BINARY_ITEM, asset="CN_LPR"),
            ]),
            mock.patch.object(ha, "_fetch_financial_challenges") as fin_fetch,
            mock.patch.object(ha, "_fetch_macro_challenges") as macro_fetch,
            mock.patch.object(ha, "creds", return_value={}),
            mock.patch.object(ha, "out") as mock_out,
        ):
            ha.cmd_challenges(args)
        fin_fetch.assert_not_called()
        macro_fetch.assert_not_called()
        payload = mock_out.call_args[0][0]
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["by_track"]["civic_forecast"], 2)
        numeric_hint = next(i for i in payload["items"] if i["outcome_shape"] == "numeric_distribution")
        binary_hint = next(i for i in payload["items"] if i["outcome_shape"] == "binary_probability")
        self.assertIn("--mean", numeric_hint["submit_hint"])
        self.assertIn("--yes-probability", binary_hint["submit_hint"])
        self.assertNotIn("--predicted-value", binary_hint["submit_hint"])

    def test_asset_filter_applies_to_civic_track(self):
        args = mock.Mock(track="civic", asset=["CN_LPR"], status="open", public=True)
        with (
            mock.patch.object(ha, "_fetch_civic_challenges", return_value=[
                dict(NUMERIC_ITEM, asset="CPI"), dict(BINARY_ITEM, asset="CN_LPR"),
            ]),
            mock.patch.object(ha, "creds", return_value={}),
            mock.patch.object(ha, "out") as mock_out,
        ):
            ha.cmd_challenges(args)
        payload = mock_out.call_args[0][0]
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["asset"], "CN_LPR")

    def test_invalid_track_value_rejected(self):
        args = mock.Mock(track="bogus", asset=None, status="open", public=True)
        with self.assertRaises(ha.HAFailure):
            ha.cmd_challenges(args)


class ForecastArgs:
    def __init__(self, **kw):
        self.challenge_id = kw.get("challenge_id", "c1")
        self.mean = kw.get("mean")
        self.std = kw.get("std")
        self.yes_probability = kw.get("yes_probability")
        self.probability = kw.get("probability")
        self.amount = kw.get("amount", 10)
        self.rationale = kw.get("rationale")
        self.expected_revision = kw.get("expected_revision")
        self.idempotency_key = kw.get("idempotency_key")
        self.bin = kw.get("bin")
        self.bin_label = kw.get("bin_label")


class BuildForecastPayloadTests(unittest.TestCase):
    def test_numeric_shape(self):
        payload = ha._build_forecast_payload(
            "numeric_distribution", ForecastArgs(mean=3.4, std=0.15), NUMERIC_ITEM
        )
        self.assertEqual(payload, {"mean": 3.4, "std": 0.15})

    def test_numeric_shape_missing_mean_fails(self):
        with self.assertRaises(ha.HAFailure):
            ha._build_forecast_payload("numeric_distribution", ForecastArgs(std=0.15), NUMERIC_ITEM)

    def test_numeric_shape_non_positive_std_fails(self):
        with self.assertRaises(ha.HAFailure):
            ha._build_forecast_payload(
                "numeric_distribution", ForecastArgs(mean=3.4, std=0), NUMERIC_ITEM
            )

    def test_binary_shape(self):
        payload = ha._build_forecast_payload(
            "binary_probability", ForecastArgs(yes_probability=0.6), BINARY_ITEM
        )
        self.assertEqual(payload, {"yes_probability": 0.6})

    def test_binary_shape_out_of_range_fails(self):
        with self.assertRaises(ha.HAFailure):
            ha._build_forecast_payload(
                "binary_probability", ForecastArgs(yes_probability=1.5), BINARY_ITEM
            )

    def test_binary_shape_missing_fails(self):
        with self.assertRaises(ha.HAFailure):
            ha._build_forecast_payload("binary_probability", ForecastArgs(), BINARY_ITEM)

    def test_ordered_shape(self):
        payload = ha._build_forecast_payload(
            "ordered_categorical_distribution",
            ForecastArgs(probability=["down=0.2", "flat=0.3", "up=0.5"]),
            ORDERED_ITEM,
        )
        self.assertEqual(payload, {"probabilities": {"down": 0.2, "flat": 0.3, "up": 0.5}})

    def test_ordered_shape_missing_category_fails(self):
        with self.assertRaises(ha.HAFailure):
            ha._build_forecast_payload(
                "ordered_categorical_distribution",
                ForecastArgs(probability=["down=0.5", "up=0.5"]),  # missing "flat"
                ORDERED_ITEM,
            )

    def test_ordered_shape_unknown_category_fails(self):
        with self.assertRaises(ha.HAFailure):
            ha._build_forecast_payload(
                "ordered_categorical_distribution",
                ForecastArgs(probability=["down=0.3", "flat=0.3", "sideways=0.4"]),
                ORDERED_ITEM,
            )

    def test_ordered_shape_bad_pair_format_fails(self):
        with self.assertRaises(ha.HAFailure):
            ha._build_forecast_payload(
                "ordered_categorical_distribution", ForecastArgs(probability=["not-a-pair"]), ORDERED_ITEM
            )

    def test_unrecognized_shape_fails_with_update_hint(self):
        with self.assertRaises(ha.HAFailure) as ctx:
            ha._build_forecast_payload("some_future_shape", ForecastArgs(), NUMERIC_ITEM)
        self.assertIn("update-check", str(ctx.exception))


class CmdForecastTests(unittest.TestCase):
    def test_rejects_client_bin(self):
        with self.assertRaises(ha.HAFailure):
            ha.cmd_forecast(ForecastArgs(bin="A"))

    def test_rejects_client_bin_label(self):
        with self.assertRaises(ha.HAFailure):
            ha.cmd_forecast(ForecastArgs(bin_label="A"))

    def test_rejects_non_positive_amount(self):
        with self.assertRaises(ha.HAFailure):
            ha.cmd_forecast(ForecastArgs(amount=0, yes_probability=0.5))

    def test_full_flow_binary_shape(self):
        calls = []

        def fake_http(method, url, body=None, token=None, agent_id=None):
            calls.append(("http", method, url))
            return 200, {"challenge": BINARY_ITEM}

        def fake_authed(method, path, body=None):
            calls.append(("authed", method, path, body))
            return 200, {"ok": True, "forecast_id": "f1", "bin_label": "YES"}

        with (
            mock.patch.object(ha, "http", side_effect=fake_http),
            mock.patch.object(ha, "authed", side_effect=fake_authed),
            mock.patch.object(ha, "out") as mock_out,
        ):
            ha.cmd_forecast(ForecastArgs(challenge_id="c-binary", yes_probability=0.6, amount=10))

        self.assertEqual(calls[0][0], "http")
        self.assertIn("c-binary", calls[0][2])
        self.assertEqual(calls[1][0], "authed")
        self.assertEqual(calls[1][2], "/eval/human-forecasts/challenges/c-binary/forecast")
        body = calls[1][3]
        self.assertEqual(body["forecast"], {"yes_probability": 0.6})
        self.assertEqual(body["amount"], 10)
        self.assertIn("idempotency_key", body)
        mock_out.assert_called_once_with({"ok": True, "forecast_id": "f1", "bin_label": "YES"})

    def test_uses_caller_supplied_idempotency_key(self):
        def fake_http(method, url, body=None, token=None, agent_id=None):
            return 200, {"challenge": NUMERIC_ITEM}

        captured = {}

        def fake_authed(method, path, body=None):
            captured["body"] = body
            return 200, {"ok": True}

        with (
            mock.patch.object(ha, "http", side_effect=fake_http),
            mock.patch.object(ha, "authed", side_effect=fake_authed),
            mock.patch.object(ha, "out"),
        ):
            ha.cmd_forecast(ForecastArgs(
                challenge_id="c-numeric", mean=3.4, std=0.15, amount=10, idempotency_key="my-key-123",
            ))
        self.assertEqual(captured["body"]["idempotency_key"], "my-key-123")

    def test_expected_revision_included_when_set(self):
        def fake_http(method, url, body=None, token=None, agent_id=None):
            return 200, {"challenge": NUMERIC_ITEM}

        captured = {}

        def fake_authed(method, path, body=None):
            captured["body"] = body
            return 200, {"ok": True}

        with (
            mock.patch.object(ha, "http", side_effect=fake_http),
            mock.patch.object(ha, "authed", side_effect=fake_authed),
            mock.patch.object(ha, "out"),
        ):
            ha.cmd_forecast(ForecastArgs(
                challenge_id="c-numeric", mean=3.4, std=0.15, amount=10, expected_revision=2,
            ))
        self.assertEqual(captured["body"]["expected_revision"], 2)

    def test_missing_scope_gives_friendly_message(self):
        def fake_http(method, url, body=None, token=None, agent_id=None):
            return 200, {"challenge": BINARY_ITEM}

        def fake_authed(method, path, body=None):
            return 403, {"detail": "missing scope credits:stake"}

        with (
            mock.patch.object(ha, "http", side_effect=fake_http),
            mock.patch.object(ha, "authed", side_effect=fake_authed),
        ):
            with self.assertRaises(ha.HAFailure):
                ha.cmd_forecast(ForecastArgs(challenge_id="c-binary", yes_probability=0.6, amount=10))

    def test_discovery_404_surfaces_as_failure_not_a_crash(self):
        with mock.patch.object(ha, "http", return_value=(404, {"detail": "not found"})):
            with self.assertRaises(ha.HAFailure):
                ha.cmd_forecast(ForecastArgs(challenge_id="nope", yes_probability=0.5, amount=10))


class MacroPredictShapeMismatchHintTests(unittest.TestCase):
    """macro-predict remains numeric-only; a binary/ordered target now gets a
    clear redirect instead of just the raw backend validation error."""

    def test_redirects_to_forecast_on_yes_probability_mismatch(self):
        def fake_authed(method, path, body=None):
            if path == "/eval/macro/challenges/c1/predict":
                return 404, {"detail": "Challenge not found"}
            return 400, {"detail": "Binary forecast requires exactly yes_probability"}

        with mock.patch.object(ha, "authed", side_effect=fake_authed):
            with self.assertRaises(ha.HAFailure) as ctx:
                ha.cmd_macro_predict(mock.Mock(
                    challenge_id="c1", predicted_value=3.4, predicted_std=0.15, amount=10, rationale=None,
                ))
        self.assertIn("ha.py forecast", str(ctx.exception))

    def test_redirects_to_forecast_on_probabilities_mismatch(self):
        def fake_authed(method, path, body=None):
            if path == "/eval/macro/challenges/c1/predict":
                return 404, {"detail": "Challenge not found"}
            return 400, {"detail": "Ordered forecast requires exactly probabilities"}

        with mock.patch.object(ha, "authed", side_effect=fake_authed):
            with self.assertRaises(ha.HAFailure) as ctx:
                ha.cmd_macro_predict(mock.Mock(
                    challenge_id="c1", predicted_value=3.4, predicted_std=0.15, amount=10, rationale=None,
                ))
        self.assertIn("ha.py forecast", str(ctx.exception))

    def test_unrelated_400_not_redirected(self):
        """A genuinely numeric validation error must still surface as itself
        — the redirect only fires on the specific shape-mismatch strings."""
        def fake_authed(method, path, body=None):
            if path == "/eval/macro/challenges/c1/predict":
                return 404, {"detail": "Challenge not found"}
            return 400, {"detail": "mean must be within the target range"}

        with mock.patch.object(ha, "authed", side_effect=fake_authed):
            with self.assertRaises(ha.HAFailure) as ctx:
                ha.cmd_macro_predict(mock.Mock(
                    challenge_id="c1", predicted_value=3.4, predicted_std=0.15, amount=10, rationale=None,
                ))
        self.assertNotIn("ha.py forecast", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
