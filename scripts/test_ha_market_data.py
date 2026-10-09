import argparse
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import ha
from ha_client.market_data import read_sse


class MarketDataTests(unittest.TestCase):
    def test_markets_discovers_without_auth(self):
        output = io.StringIO()
        with mock.patch.object(ha, "http", return_value=(200, {"assets": [{"asset": "GC"}]})) as read, contextlib.redirect_stdout(output):
            ha.cmd_markets(argparse.Namespace())
        self.assertEqual(json.loads(output.getvalue())["assets"][0]["asset"], "GC")
        self.assertTrue(read.call_args.args[1].endswith('/eval/market-assets'))

    def test_challenges_bundle_once_per_asset_without_changing_price_event_schema(self):
        args = argparse.Namespace(track="all", asset=None, status="open", public=True)
        financial = [{"id": "one", "asset": "GC", "status": "open"}, {"id": "two", "asset": "GC", "status": "open"}]
        price = [{"id": "price", "asset": "ETH", "prediction_schema": {"type": "binary_probability"}}]
        output = io.StringIO()
        with mock.patch.object(ha, "_fetch_financial_challenges", return_value=financial), \
             mock.patch.object(ha, "_fetch_civic_challenges", return_value=[]), \
             mock.patch.object(ha, "_fetch_price_event_challenges", return_value=price), \
             mock.patch.object(ha, "_fetch_market_context", return_value={"quote": {"price": 1}, "ohlc": {"bars": []}}) as context, \
             mock.patch.object(ha, "creds", return_value={}), contextlib.redirect_stdout(output):
            ha.cmd_challenges(args)
        result = json.loads(output.getvalue())
        self.assertEqual(set(result["market_context"]), {"GC", "ETH"})
        self.assertEqual(context.call_count, 2)
        self.assertEqual(result["items"][2]["prediction_schema"]["type"], "binary_probability")

    def test_event_discovery_filters_before_building_context(self):
        args = argparse.Namespace(track="financial", asset=None, status="open", public=True,
                                  event_id="news-one")
        rows = [{"id": "one", "event_id": "news-one", "asset": "GC", "status": "open"},
                {"id": "two", "event_id": "news-two", "asset": "ETH", "status": "open"}]
        output = io.StringIO()
        with mock.patch.object(ha, "_fetch_financial_challenges", return_value=rows), \
             mock.patch.object(ha, "_fetch_market_context", return_value={}) as context, \
             mock.patch.object(ha, "creds", return_value={}), contextlib.redirect_stdout(output):
            ha.cmd_challenges(args)
        result = json.loads(output.getvalue())
        self.assertEqual([item["id"] for item in result["items"]], ["one"])
        context.assert_called_once_with("GC")

    def test_missing_context_does_not_hide_challenge(self):
        with mock.patch.object(ha, "http", return_value=(404, {})):
            result = ha._fetch_market_context("GC")
        self.assertEqual(result["error"], "context_unavailable")
        self.assertEqual(result["availability"], {"quote": False, "ohlc": False})

    def test_old_server_bar_close_is_not_misrepresented_as_a_live_quote(self):
        with mock.patch.object(ha, "http", return_value=(200, {"asset": "GC", "price": 123})):
            result = ha._fetch_market_context("GC")
        self.assertEqual(result["reason"], "market_evidence_api_upgrade_required")
        self.assertNotIn("price", result)

    def test_sse_parser_ignores_heartbeats_and_metadata_and_keeps_cursor(self):
        raw = io.BytesIO(b'retry: 5000\n\nevent: ready\ndata: {}\n\n: heartbeat\n\nevent: news\nid: first\ndata: {"id":"one"}\n\nevent: news\nid: second\ndata: {"id":"two"}\n\n')
        result = list(read_sse(raw, max_events=1))
        self.assertEqual(result, [{"cursor": "first", "event": {"id": "one"}}])

    def test_sse_deadline_stops_even_when_only_heartbeats_arrive(self):
        with mock.patch("ha_client.market_data.time.monotonic", return_value=60):
            self.assertEqual(list(read_sse(io.BytesIO(b': heartbeat\n\n'), 20, deadline=30)), [])

    def test_sse_errors_are_not_reported_as_success(self):
        with self.assertRaises(ValueError):
            list(read_sse(io.BytesIO(b'event: error\ndata: {"code":"unavailable"}\n\n'), 20))


if __name__ == "__main__":
    unittest.main()
