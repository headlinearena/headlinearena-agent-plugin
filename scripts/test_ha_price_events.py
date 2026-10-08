import argparse
import copy
import unittest
from unittest import mock
import ha
from ha_client.contracts import price_event_from_contract_entry


def entry(shape="binary_probability", status="open", id="price-1", asset="BTC"):
    return {"contract": {"api_contract_version": "prediction-contract-v2", "site": "global", "execution_family": "price_event", "submission_route": "price_event", "participation_contract": "score_only", "submission_atomic": True, "required_scopes": ["prediction:submit"], "scope_key": asset, "target_key": asset + "_PRICE_EVENTS", "outcome_shape": shape, "forecast_schema": {"input_encoding": "direction_confidence" if shape == "binary_probability" else "normal_mean_std"}}, "current_challenge": {"challenge_id": id, "status": status, "deadline": "2026-12-31", "question": {"en": "Will the threshold be met?"}}}


class PriceEventsTests(unittest.TestCase):
    def test_public_financial_discovery_reads_every_page(self):
        with mock.patch.object(ha, 'http', side_effect=[(200, {'items': [{'id': 'one'}], 'total': 2}), (200, {'items': [{'id': 'two'}], 'total': 2})]) as read:
            rows = ha._public_challenges()['items']
        self.assertEqual([r['id'] for r in rows], ['one', 'two'])
        self.assertIn('offset=1', read.call_args[0][1])

    def test_default_public_list_includes_both_shapes_and_asset_filter(self):
        rows = [entry(id="btc"), entry("numeric_distribution", id="eth", asset="ETH"), entry(status="resolved")]
        for assets, expected in [(None, ["btc", "eth"]), (["ETH"], ["eth"])]:
            args = argparse.Namespace(track="all", asset=assets, public=True, status="open", include_post_close=False)
            with mock.patch.object(ha, '_fetch_financial_challenges', return_value=[]), mock.patch.object(ha, '_fetch_civic_challenges', return_value=[]), mock.patch.object(ha, '_fetch_prediction_contract_entries', return_value=(200, rows)), mock.patch.object(ha, 'creds', return_value={}), mock.patch.object(ha, 'out') as out:
                ha.cmd_challenges(args)
            result = out.call_args[0][0]
            self.assertEqual([c['id'] for c in result['items']], expected)
            self.assertEqual(result['by_track']['price_event'], len(expected))
            for c in result['items']:
                self.assertIn('price-predict', c['submit_hint'])
                self.assertIn('no --amount', c['submit_hint'])

    def test_unknown_route_or_encoding_is_not_misrepresented(self):
        for field, value in [('submission_route', 'human_forecast'), ('participation_contract', 'forecast_and_stake')]:
            row = entry(); row['contract'][field] = value
            self.assertIsNone(price_event_from_contract_entry(row))
        row = entry(); row['contract']['forecast_schema']['input_encoding'] = 'unsupported'
        self.assertIsNone(price_event_from_contract_entry(row))

    def test_exact_submission_route_and_encoding(self):
        for shape, kwargs, expected in [('binary_probability', {'yes_probability': 0.2}, {'probabilities': {'bullish': 0.2, 'bearish': 0.8}}), ('numeric_distribution', {'mean': 80000, 'std': 1500}, {'predicted_value': 80000, 'predicted_std': 1500})]:
            args = argparse.Namespace(challenge_id="price-1", mean=None, std=None, yes_probability=None, reasoning="Analysis supported by the current market evidence.")
            for key, value in kwargs.items(): setattr(args, key, value)
            row = price_event_from_contract_entry(entry(shape))
            with mock.patch.object(ha, '_fetch_price_event_challenges', return_value=[row]), mock.patch.object(ha, 'authed', return_value=(201, {})) as post, mock.patch.object(ha, 'out'):
                ha.cmd_price_predict(args)
            self.assertEqual(post.call_args[0][0:2], ('POST', '/eval/price-events/challenges/price-1/predict'))
            self.assertEqual(post.call_args[0][2], dict(expected, reasoning=args.reasoning))
            self.assertNotIn('amount', post.call_args[0][2])

    def test_unavailable_discovery_does_not_claim_completeness(self):
        with mock.patch.object(ha, '_fetch_prediction_contract_entries', return_value=(503, [])), self.assertRaises(ha.HAFailure):
            ha._fetch_price_event_challenges()

    def test_nonfinite_or_wrong_shape_never_writes(self):
        for kwargs in [{'mean': float('nan'), 'std': 10}, {'mean': 10, 'std': 0}, {'yes_probability': 0.5}]:
            args = argparse.Namespace(challenge_id="price-1", mean=None, std=None, yes_probability=None, reasoning="A sufficiently detailed market analysis.")
            for key, value in kwargs.items(): setattr(args, key, value)
            with mock.patch.object(ha, '_fetch_price_event_challenges', return_value=[price_event_from_contract_entry(entry('numeric_distribution'))]), mock.patch.object(ha, 'authed') as post:
                with self.assertRaises(ha.HAFailure): ha.cmd_price_predict(args)
                post.assert_not_called()


if __name__ == '__main__': unittest.main()
