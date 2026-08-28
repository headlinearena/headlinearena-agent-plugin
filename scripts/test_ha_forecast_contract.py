#!/usr/bin/env python3
"""Tests for the canonical Civic Index forecast contract.

Covers:
- `_fetch_civic_challenges` (prediction-contract-v2 discovery: outcome_shape,
  forecast_schema and target_key kept — unlike the legacy
  `_fetch_civic_numeric_challenges`, which now ALSO filters to
  numeric_distribution-only, a real behavior change from 1.30.0 covered
  here and reconciled against test_ha_civic_numeric_merge.py).
- `challenges --track civic` as the canonical official-statistics discovery;
  `--track macro` is only a deprecated alias.
- `forecast`: payload construction per outcome_shape,
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


def contract_entry(item):
    schema = item["forecast_schema"]
    if item["outcome_shape"] == "ordered_categorical_distribution":
        schema = {
            "input_encoding": "ordered_probabilities",
            "outcome_shape": item["outcome_shape"],
            "required_fields": ["probabilities"],
            "categories": [
                {"key": value, "labels": {"en": value, "zh-Hans": value,
                                             "zh-Hant": value, "yue": value}}
                for value in ("down", "flat", "up")
            ],
            "probability_sum": 1,
            "tolerance": "0.000001",
            "additional_properties": False,
        }
    return {
        "contract": {
            "api_contract_version": "prediction-contract-v2",
            "target_key": item["target_key"],
            "scope_key": item["scope_key"],
            "site": "global",
            "region": item["region"],
            "execution_family": "human_forecast",
            "submission_route": "human_forecast",
            "outcome_shape": item["outcome_shape"],
            "forecast_schema": schema,
            "participation_contract": "forecast_and_stake",
            "submission_atomic": True,
            "required_scopes": ["prediction:submit", "credits:stake"],
        },
        "current_challenge": {
            "challenge_id": item["id"],
            "status": item["status"],
            "deadline": item["deadline"],
        },
    }


def v2_response(*items):
    return {
        "api_contract_version": "prediction-contract-v2",
        "entries": [contract_entry(item) for item in items],
    }


class FetchCivicChallengesTests(unittest.TestCase):
    def test_keeps_full_fidelity_fields(self):
        with mock.patch.object(ha, "http", return_value=(200, v2_response(BINARY_ITEM))):
            items = ha._fetch_civic_challenges()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["outcome_shape"], "binary_probability")
        self.assertEqual(item["forecast_schema"], BINARY_ITEM["forecast_schema"])
        self.assertEqual(item["submission_route"], "human_forecast")
        self.assertEqual(item["participation_contract"], "forecast_and_stake")
        self.assertEqual(item["target_key"], "HF_CN_LPR")
        self.assertEqual(item["asset"], "LPR")  # "HF_CN_LPR" -> drop family+region, same as legacy behavior

    def test_includes_all_shapes_unfiltered(self):
        with mock.patch.object(
            ha, "http", return_value=(200, v2_response(NUMERIC_ITEM, BINARY_ITEM, ORDERED_ITEM))
        ):
            items = ha._fetch_civic_challenges()
        self.assertEqual(len(items), 3)

    def test_non_200_status_returns_empty_list(self):
        with mock.patch.object(ha, "http", return_value=(500, {"detail": "error"})):
            items = ha._fetch_civic_challenges()
        self.assertEqual(items, [])

    def test_unknown_successful_contract_fails_closed(self):
        with mock.patch.object(
            ha,
            "http",
            return_value=(200, {"api_contract_version": "prediction-contract-v3", "entries": []}),
        ):
            with self.assertRaises(ha.HAFailure):
                ha._fetch_civic_challenges()

    def test_non_atomic_human_contract_is_not_discoverable(self):
        entry = contract_entry(BINARY_ITEM)
        entry["contract"]["submission_atomic"] = False
        response = v2_response()
        response["entries"] = [entry]
        with mock.patch.object(ha, "http", return_value=(200, response)):
            self.assertEqual(ha._fetch_civic_challenges(), [])

    def test_404_route_uses_legacy_rolling_deploy_fallback(self):
        with mock.patch.object(
            ha,
            "http",
            side_effect=[
                (404, {"detail": "not found"}),
                (200, {"challenges": [BINARY_ITEM]}),
                (200, {"challenges": []}),
            ],
        ) as mocked:
            items = ha._fetch_civic_challenges()
        self.assertEqual(items[0]["id"], "c-binary")
        self.assertEqual(mocked.call_count, 3)

    def test_open_legacy_macro_round_is_projected_into_civic(self):
        legacy = {
            "id": "legacy-cpi",
            "asset": "CPI",
            "canonical_target_key": "HF_US_CPI",
            "status": "open",
            "deadline": "2026-09-10T12:00:00Z",
            "compatibility_status": "legacy_open_round",
        }
        with mock.patch.object(
            ha,
            "http",
            side_effect=[
                (200, v2_response()),
                (200, {"challenges": [legacy]}),
            ],
        ):
            items = ha._fetch_civic_challenges()
        self.assertEqual(items[0]["target_key"], "HF_US_CPI")
        self.assertEqual(items[0]["submission_route"], "macro_numeric_legacy")
        self.assertEqual(items[0]["outcome_shape"], "numeric_distribution")

    def test_deprecated_macro_endpoint_failure_does_not_hide_v2_items(self):
        with mock.patch.object(
            ha,
            "http",
            side_effect=[
                (200, v2_response(BINARY_ITEM)),
                (503, {"detail": "legacy unavailable"}),
            ],
        ):
            items = ha._fetch_civic_challenges()
        self.assertEqual([item["id"] for item in items], ["c-binary"])

    def test_v2_projection_wins_when_legacy_list_duplicates_round(self):
        entry = contract_entry(NUMERIC_ITEM)
        entry["contract"].update(
            execution_family="macro_numeric",
            submission_route="macro_numeric_legacy",
            compatibility_status="legacy_open_round",
        )
        response = v2_response()
        response["entries"] = [entry]
        with mock.patch.object(
            ha,
            "http",
            side_effect=[
                (200, response),
                (200, {"challenges": [{"id": "c-numeric", "asset": "CPI"}]}),
            ],
        ):
            items = ha._fetch_civic_challenges()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["target_key"], "HF_US_CPI")
        self.assertEqual(items[0]["compatibility_status"], "legacy_open_round")


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

    def test_numeric_shape_rejects_non_finite_values(self):
        with self.assertRaises(ha.HAFailure):
            ha._build_forecast_payload(
                "numeric_distribution", ForecastArgs(mean=float("nan"), std=0.2), NUMERIC_ITEM
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

    def test_binary_shape_rejects_nan(self):
        with self.assertRaises(ha.HAFailure):
            ha._build_forecast_payload(
                "binary_probability", ForecastArgs(yes_probability=float("nan")), BINARY_ITEM
            )

    def test_binary_shape_missing_fails(self):
        with self.assertRaises(ha.HAFailure):
            ha._build_forecast_payload("binary_probability", ForecastArgs(), BINARY_ITEM)

    def test_ordered_shape(self):
        ordered_v2 = ha._civic_from_contract_entry(contract_entry(ORDERED_ITEM))
        payload = ha._build_forecast_payload(
            "ordered_categorical_distribution",
            ForecastArgs(probability=["down=0.2", "flat=0.3", "up=0.5"]),
            ordered_v2,
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

    def test_ordered_shape_rejects_duplicate_category(self):
        with self.assertRaises(ha.HAFailure):
            ha._build_forecast_payload(
                "ordered_categorical_distribution",
                ForecastArgs(probability=["down=0.2", "down=0.3", "flat=0.1", "up=0.4"]),
                ORDERED_ITEM,
            )

    def test_ordered_shape_rejects_probability_sum_not_one(self):
        ordered_v2 = ha._civic_from_contract_entry(contract_entry(ORDERED_ITEM))
        with self.assertRaises(ha.HAFailure):
            ha._build_forecast_payload(
                "ordered_categorical_distribution",
                ForecastArgs(probability=["down=0.2", "flat=0.2", "up=0.2"]),
                ordered_v2,
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

        def fake_authed(method, path, body=None):
            calls.append(("authed", method, path, body))
            return 200, {"ok": True, "forecast_id": "f1", "bin_label": "YES"}

        with (
            mock.patch.object(
                ha, "_fetch_prediction_contract_entries",
                return_value=(200, [contract_entry(BINARY_ITEM)]),
            ),
            mock.patch.object(ha, "authed", side_effect=fake_authed),
            mock.patch.object(ha, "out") as mock_out,
        ):
            ha.cmd_forecast(ForecastArgs(challenge_id="c-binary", yes_probability=0.6, amount=10))

        self.assertEqual(calls[0][0], "authed")
        self.assertEqual(calls[0][2], "/eval/human-forecasts/challenges/c-binary/forecast")
        body = calls[0][3]
        self.assertEqual(body["forecast"], {"yes_probability": 0.6})
        self.assertEqual(body["amount"], 10)
        self.assertIn("idempotency_key", body)
        mock_out.assert_called_once_with({"ok": True, "forecast_id": "f1", "bin_label": "YES"})

    def test_uses_caller_supplied_idempotency_key(self):
        captured = {}

        def fake_authed(method, path, body=None):
            captured["body"] = body
            return 200, {"ok": True}

        with (
            mock.patch.object(
                ha, "_fetch_prediction_contract_entries",
                return_value=(200, [contract_entry(NUMERIC_ITEM)]),
            ),
            mock.patch.object(ha, "authed", side_effect=fake_authed),
            mock.patch.object(ha, "out"),
        ):
            ha.cmd_forecast(ForecastArgs(
                challenge_id="c-numeric", mean=3.4, std=0.15, amount=10, idempotency_key="my-key-123",
            ))
        self.assertEqual(captured["body"]["idempotency_key"], "my-key-123")

    def test_expected_revision_included_when_set(self):
        captured = {}

        def fake_authed(method, path, body=None):
            captured["body"] = body
            return 200, {"ok": True}

        with (
            mock.patch.object(
                ha, "_fetch_prediction_contract_entries",
                return_value=(200, [contract_entry(NUMERIC_ITEM)]),
            ),
            mock.patch.object(ha, "authed", side_effect=fake_authed),
            mock.patch.object(ha, "out"),
        ):
            ha.cmd_forecast(ForecastArgs(
                challenge_id="c-numeric", mean=3.4, std=0.15, amount=10, expected_revision=2,
            ))
        self.assertEqual(captured["body"]["expected_revision"], 2)

    def test_missing_scope_gives_friendly_message(self):
        def fake_authed(method, path, body=None):
            return 403, {"detail": "missing scope credits:stake"}

        with (
            mock.patch.object(
                ha, "_fetch_prediction_contract_entries",
                return_value=(200, [contract_entry(BINARY_ITEM)]),
            ),
            mock.patch.object(ha, "authed", side_effect=fake_authed),
        ):
            with self.assertRaises(ha.HAFailure):
                ha.cmd_forecast(ForecastArgs(challenge_id="c-binary", yes_probability=0.6, amount=10))

    def test_v2_missing_challenge_fails_before_submit(self):
        with mock.patch.object(
            ha, "_fetch_prediction_contract_entries", return_value=(200, [])
        ), mock.patch.object(ha, "_fetch_macro_challenges", return_value=[]), \
             mock.patch.object(ha, "authed") as authed:
            with self.assertRaises(ha.HAFailure):
                ha.cmd_forecast(ForecastArgs(challenge_id="nope", yes_probability=0.5, amount=10))
        authed.assert_not_called()

    def test_forecast_transparently_submits_open_legacy_round(self):
        legacy = {
            "id": "legacy-cpi",
            "asset": "CPI",
            "canonical_target_key": "HF_US_CPI",
            "status": "open",
        }
        with (
            mock.patch.object(ha, "_fetch_prediction_contract_entries", return_value=(200, [])),
            mock.patch.object(ha, "_fetch_macro_challenges", return_value=[legacy]),
            mock.patch.object(ha, "authed", return_value=(200, {"ok": True})) as authed,
            mock.patch.object(ha, "out"),
        ):
            ha.cmd_forecast(ForecastArgs(
                challenge_id="legacy-cpi", mean=3.1, std=0.2, amount=25,
                rationale="official release model",
            ))
        path = authed.call_args[0][1]
        body = authed.call_args[0][2]
        self.assertEqual(path, "/eval/macro/challenges/legacy-cpi/predict")
        self.assertEqual(body, {
            "predicted_value": 3.1,
            "predicted_std": 0.2,
            "amount": 25,
            "rationale": "official release model",
        })

    def test_forecast_uses_frozen_legacy_route_from_v2_projection(self):
        entry = contract_entry(NUMERIC_ITEM)
        entry["contract"].update(
            execution_family="macro_numeric",
            submission_route="macro_numeric_legacy",
            compatibility_status="legacy_open_round",
        )
        with (
            mock.patch.object(
                ha, "_fetch_prediction_contract_entries", return_value=(200, [entry])
            ),
            mock.patch.object(ha, "authed", return_value=(200, {"ok": True})) as authed,
            mock.patch.object(ha, "out"),
        ):
            ha.cmd_forecast(ForecastArgs(
                challenge_id="c-numeric", mean=3.1, std=0.2, amount=25,
            ))
        self.assertEqual(
            authed.call_args[0][1], "/eval/macro/challenges/c-numeric/predict"
        )


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
