#!/usr/bin/env python3
"""Regression coverage for discoverable post-close financial paper-trade signals.

Run: python3 scripts/test_ha_paper_signals.py
"""
import argparse
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ha  # noqa: E402


OPEN = {"id": "open-gc", "asset": "GC", "status": "open"}
CLOSED = {"id": "closed-gc", "asset": "GC", "status": "closed"}
RESOLVED = {"id": "resolved-es", "asset": "ES", "status": "resolved"}


class PostCloseDiscoveryTests(unittest.TestCase):
    def test_include_post_close_combines_active_closed_and_resolved(self):
        args = argparse.Namespace(public=False, status="open", include_post_close=True)
        with mock.patch.object(ha, "creds", return_value={"agent_id": "a", "client_secret": "s"}), \
             mock.patch.object(ha, "authed", return_value=(200, {"challenges": [{"challenge": OPEN}]})), \
             mock.patch.object(
                 ha,
                 "http",
                 side_effect=[(200, {"items": [CLOSED]}), (200, {"items": [RESOLVED]})],
             ) as http:
            items = ha._fetch_financial_challenges(args)

        self.assertEqual([item["id"] for item in items], ["open-gc", "closed-gc", "resolved-es"])
        self.assertIn("status=closed", http.call_args_list[0].args[1])
        self.assertIn("status=resolved", http.call_args_list[1].args[1])

    def test_post_close_items_are_explicitly_unscored(self):
        args = argparse.Namespace(
            track="financial", asset=None, status="open", public=True, include_post_close=True
        )
        with mock.patch.object(ha, "_fetch_financial_challenges", return_value=[OPEN, CLOSED]), \
             mock.patch.object(ha, "creds", return_value={}), \
             mock.patch.object(ha, "out") as out:
            ha.cmd_challenges(args)

        payload = out.call_args.args[0]
        paper = next(item for item in payload["items"] if item["id"] == "closed-gc")
        self.assertEqual(paper["submission_mode"], "paper_trade")
        self.assertFalse(paper["counts_for_score"])
        self.assertIn("no --amount", paper["submit_hint"])
        self.assertIn("paper-signals", payload["paper_trade_hint"])


class PaperSignalCommandTests(unittest.TestCase):
    def test_reads_only_own_paper_signal_history(self):
        args = argparse.Namespace(challenge_id="closed-gc", limit=20, cursor="2026-09-06T00:00:00")
        response = {"items": [{"id": "sig-1", "direction": "bullish"}], "next_cursor": None}
        with mock.patch.object(ha, "authed", return_value=(200, response)) as authed, \
             mock.patch.object(ha, "out") as out:
            ha.cmd_paper_signals(args)

        self.assertEqual(
            authed.call_args.args[:2],
            ("GET", "/eval/challenges/closed-gc/paper-signals?limit=20&cursor=2026-09-06T00%3A00%3A00"),
        )
        self.assertEqual(out.call_args.args[0], response)

    def test_late_predict_announces_that_it_is_unscored(self):
        args = argparse.Namespace(
            challenge_id="closed-gc", direction="bullish", confidence=0.7,
            reasoning="Momentum remains positive.", revision=False, summary=None, amount=None,
        )
        response = {"challenge_id": "closed-gc", "counts_for_score": False}
        with mock.patch.object(ha, "authed", return_value=(201, response)), \
             mock.patch.object(ha, "note") as note, \
             mock.patch.object(ha, "out"):
            ha.cmd_predict(args)

        self.assertIn("Paper-trade signal recorded", note.call_args.args[0])
        self.assertIn("paper-signals closed-gc", note.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
