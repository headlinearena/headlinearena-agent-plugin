#!/usr/bin/env python3
"""Tests for _absorb_status_headers: passive claim-state sync from the
X-HA-Agent-Status response header (backend v3.186.0+) after every authed()
call — the fix for operators claiming in the browser while the agent-side
cached status stayed "unclaimed" until someone ran an explicit
`ha.py status` poll.

Stdlib-only (unittest + unittest.mock). Run: python3 scripts/test_ha_status_headers.py
"""
import os
import sys
import unittest
from email.message import Message
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ha  # noqa: E402


def _headers(agent_status=None):
    msg = Message()
    if agent_status:
        msg["X-HA-Agent-Status"] = agent_status
        msg["X-HA-Verification-Status"] = (
            "verified" if agent_status == "active" else "pending"
        )
    return msg


class AbsorbStatusHeadersTests(unittest.TestCase):
    def setUp(self):
        self.entry = {
            "agent_id": "agt_t",
            "client_secret": "sec",
            "status": "active_provisional",
            "claim_url": "https://x/claim/abc",
            "pairing_code": "AAA-BBB",
        }
        self._patches = [
            mock.patch.object(ha, "creds", return_value=self.entry),
            mock.patch.object(ha, "update_creds"),
            mock.patch.object(ha, "note"),
        ]
        _, self.update_creds, self.note = [p.start() for p in self._patches]

    def tearDown(self):
        for p in self._patches:
            p.stop()
        ha._last_response_headers = None

    def test_claimed_header_flips_status_and_drops_claim_artifacts(self):
        ha._last_response_headers = _headers("active")
        ha._absorb_status_headers()
        self.update_creds.assert_called_once_with(
            status="active", challenge=None, claim_url=None, pairing_code=None
        )
        self.note.assert_called_once()

    def test_matching_status_is_a_noop(self):
        ha._last_response_headers = _headers("active_provisional")
        ha._absorb_status_headers()
        self.update_creds.assert_not_called()

    def test_missing_header_is_a_noop(self):
        # Older backends never send the header — nothing must change.
        ha._last_response_headers = _headers(None)
        ha._absorb_status_headers()
        self.update_creds.assert_not_called()

    def test_no_response_yet_is_a_noop(self):
        ha._last_response_headers = None
        ha._absorb_status_headers()
        self.update_creds.assert_not_called()

    def test_non_active_change_syncs_without_clearing_claim(self):
        self.entry["status"] = "pending"
        ha._last_response_headers = _headers("active_provisional")
        ha._absorb_status_headers()
        self.update_creds.assert_called_once_with(status="active_provisional")
        self.note.assert_not_called()


if __name__ == "__main__":
    unittest.main()
