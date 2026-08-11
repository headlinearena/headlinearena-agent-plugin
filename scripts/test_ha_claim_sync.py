#!/usr/bin/env python3
"""Regression tests for _sync_claim_status not gating on a stale local
`challenge` cache.

Real-world bug (reported against v1.27.6 on Hermes): an agent whose
registration challenge got resolved out-of-band (e.g. an LLM agent POSTing
straight to the challenge's submit_url instead of calling
`ha.py challenge-submit`) never has its local credentials.json `challenge`
field cleared, even though the backend correctly progresses it through
active_provisional -> active. _sync_claim_status used to require
`not entry.get("challenge")` before attempting ANY network check, so it
returned the stale cached entry instantly (0 network calls, no error) for
the rest of that agent's life -- indistinguishable from "genuinely still
unclaimed" even well after a real browser claim succeeded.

Stdlib-only (unittest + unittest.mock). Run: python3 scripts/test_ha_claim_sync.py
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ha  # noqa: E402


class StaleChallengeTests(unittest.TestCase):
    def setUp(self):
        self.entry = {
            "agent_id": "agt_test",
            "client_secret": "secret",
            "status": None,
            # Stale: the challenge was actually resolved out-of-band, but
            # nothing in this local cache ever recorded that.
            "challenge": {"challenge_id": "c1", "submit_url": "https://x/submit"},
        }
        self._patches = [
            mock.patch.object(ha, "authed"),
            mock.patch.object(ha, "update_creds"),
            mock.patch.object(ha, "creds"),
            mock.patch.object(ha, "note"),
        ]
        self.mocks = {p.attribute: p.start() for p in self._patches}
        for p in self._patches:
            self.addCleanup(p.stop)

    def test_attempts_profile_self_despite_stale_challenge_field(self):
        self.mocks["authed"].return_value = (200, {"verification_status": "verified"})
        self.mocks["creds"].return_value = {**self.entry, "status": "active", "challenge": None}

        result = ha._sync_claim_status(self.entry, light=True)

        self.mocks["authed"].assert_called_once_with("GET", "/agent/profile/self")
        self.mocks["update_creds"].assert_called_once_with(status="active", challenge=None)
        self.assertEqual(result["status"], "active")

    def test_still_returns_cached_entry_unchanged_when_genuinely_not_yet_verified(self):
        self.mocks["authed"].return_value = (200, {"verification_status": "pending"})

        result = ha._sync_claim_status(self.entry, light=True)

        self.mocks["authed"].assert_called_once()
        self.mocks["update_creds"].assert_not_called()
        self.assertEqual(result, self.entry)

    def test_missing_credentials_still_short_circuits(self):
        entry = {"challenge": {"challenge_id": "c1"}}  # no agent_id/client_secret at all
        result = ha._sync_claim_status(entry, light=True)
        self.mocks["authed"].assert_not_called()
        self.assertEqual(result, entry)


class GetTokenClearsStaleChallengeTests(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.object(ha, "creds"),
            mock.patch.object(ha, "http"),
            mock.patch.object(ha, "update_creds"),
        ]
        self.mocks = {}
        for p in self._patches:
            name = p.attribute
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)

    def test_successful_token_issuance_clears_challenge(self):
        self.mocks["creds"].return_value = {
            "agent_id": "agt_test", "client_secret": "secret", "token": None,
            "challenge": {"challenge_id": "c1"},
        }
        self.mocks["http"].return_value = (200, {
            "access_token": "tok123", "expires_in": 3600, "agent_status": "active",
        })

        result = ha.get_token(force=True)

        self.assertEqual(result, "tok123")
        _, kwargs = self.mocks["update_creds"].call_args
        self.assertIsNone(kwargs["challenge"])
        self.assertEqual(kwargs["status"], "active")


if __name__ == "__main__":
    unittest.main()
