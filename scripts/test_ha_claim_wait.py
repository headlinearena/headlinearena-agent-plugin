#!/usr/bin/env python3
"""Tests for _wait_for_claim: the `status --wait` rework onto the backend
claim long-poll (POST /agent/registry/claim/wait, v3.187.0+), with fallback
to legacy profile/self polling on older backends.

Stdlib-only (unittest + unittest.mock). Run: python3 scripts/test_ha_claim_wait.py
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ha  # noqa: E402


class WaitForClaimTests(unittest.TestCase):
    def setUp(self):
        self.entry = {
            "agent_id": "agt_t",
            "client_secret": "sec",
            "status": "active_provisional",
            "claim_url": "https://x/claim/abc",
            "pairing_code": "AAA-BBB",
        }
        self.claimed_entry = dict(self.entry, status="active")
        self._patches = {
            "update_creds": mock.patch.object(ha, "update_creds"),
            "note": mock.patch.object(ha, "note"),
            "sleep": mock.patch.object(time, "sleep"),
        }
        self.m = {k: p.start() for k, p in self._patches.items()}

    def tearDown(self):
        for p in self._patches.values():
            p.stop()

    def test_longpoll_claim_detected(self):
        responses = iter([
            (200, {"claimed": False, "agent_status": "active_provisional", "waited_seconds": 55}),
            (200, {"claimed": True, "agent_status": "active", "waited_seconds": 3.2}),
        ])
        with mock.patch.object(ha, "http", side_effect=lambda *a, **k: next(responses)) as http, \
             mock.patch.object(ha, "creds", return_value=self.claimed_entry):
            result = ha._wait_for_claim(dict(self.entry), time.time() + 600, 5)
        self.assertEqual(result["status"], "active")
        self.m["update_creds"].assert_called_once_with(
            status="active", challenge=None, claim_url=None, pairing_code=None
        )
        # long-poll path never sleeps client-side
        self.m["sleep"].assert_not_called()
        # each call asks the server to hold, capped at 55s
        body = http.call_args_list[0][0][2]
        self.assertEqual(body["timeout_seconds"], 55)
        self.assertEqual(body["agent_id"], "agt_t")

    def test_fallback_to_legacy_polling_on_404(self):
        with mock.patch.object(ha, "http", return_value=(404, {"detail": "Not Found"})) as http, \
             mock.patch.object(ha, "_sync_claim_status",
                               return_value=self.claimed_entry) as sync:
            result = ha._wait_for_claim(dict(self.entry), time.time() + 600, 5)
        self.assertEqual(result["status"], "active")
        self.assertEqual(http.call_count, 1)  # long-poll tried once, then abandoned
        sync.assert_called_once()
        self.m["sleep"].assert_called_once()

    def test_private_key_jwt_agent_skips_longpoll(self):
        entry = dict(self.entry)
        del entry["client_secret"]
        with mock.patch.object(ha, "http") as http, \
             mock.patch.object(ha, "_sync_claim_status",
                               return_value=dict(entry, status="active")):
            result = ha._wait_for_claim(entry, time.time() + 600, 5)
        self.assertEqual(result["status"], "active")
        http.assert_not_called()

    def test_deadline_expires_unclaimed(self):
        unclaimed = (200, {"claimed": False, "agent_status": "active_provisional",
                           "waited_seconds": 1})
        with mock.patch.object(ha, "http", return_value=unclaimed):
            result = ha._wait_for_claim(dict(self.entry), time.time() + 0.2, 5)
        self.assertEqual(result["status"], "active_provisional")
        self.m["update_creds"].assert_not_called()

    def test_already_active_returns_without_any_call(self):
        with mock.patch.object(ha, "http") as http:
            result = ha._wait_for_claim(dict(self.claimed_entry), time.time() + 600, 5)
        self.assertEqual(result["status"], "active")
        http.assert_not_called()

    def test_status_change_without_claim_is_synced(self):
        responses = iter([
            (200, {"claimed": False, "agent_status": "suspended", "waited_seconds": 2}),
        ])
        synced = dict(self.entry, status="suspended")
        with mock.patch.object(ha, "http", side_effect=lambda *a, **k: next(responses)), \
             mock.patch.object(ha, "creds", return_value=synced):
            result = ha._wait_for_claim(dict(self.entry), time.time() + 0.5, 5)
        self.assertEqual(result["status"], "suspended")
        self.m["update_creds"].assert_called_once_with(status="suspended")


if __name__ == "__main__":
    unittest.main()
