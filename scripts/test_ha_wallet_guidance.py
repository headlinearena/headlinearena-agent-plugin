#!/usr/bin/env python3
"""Tests for wallet-funding guidance in `ha.py status`.

Covers the two false-positive paths found during a full-coverage live
forecast run on 2026-10-06 (46 civic forecasts, whole wallet staked):

1. `_credits_look_unfunded` ignored frozen_balance, so a fully-staked agent
   (available 0 / frozen 2300) was told to set up its wallet on every
   status call.
2. `_wallet_setup_guidance` treated any non-list granted_scopes payload as
   "wallet:manage missing" and advised a self-grant the agent already held.
   Unknown payload shapes must now produce a neutral check-first message.

Stdlib-only (unittest + unittest.mock). Run:
  python3 scripts/test_ha_wallet_guidance.py
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ha  # noqa: E402


class CreditsLookUnfundedTests(unittest.TestCase):
    def test_frozen_balance_counts_as_funded(self):
        # The live case: everything staked on open forecasts.
        self.assertFalse(
            ha._credits_look_unfunded(
                {"available_balance": 0.0, "frozen_balance": 2300.0}
            )
        )

    def test_available_balance_positive_is_funded(self):
        self.assertFalse(ha._credits_look_unfunded({"available_balance": 12.5}))

    def test_plain_balance_positive_is_funded(self):
        self.assertFalse(ha._credits_look_unfunded({"balance": 5}))

    def test_all_zero_is_unfunded(self):
        self.assertTrue(
            ha._credits_look_unfunded({"available_balance": 0, "frozen_balance": 0})
        )

    def test_non_numeric_values_are_unfunded(self):
        self.assertTrue(ha._credits_look_unfunded({"available_balance": "n/a"}))

    def test_missing_or_string_payload_is_unfunded(self):
        self.assertTrue(ha._credits_look_unfunded(None))
        self.assertTrue(ha._credits_look_unfunded("n/a — missing credits:read"))


class GrantedScopeListTests(unittest.TestCase):
    def test_list_passes_through(self):
        scopes = ["prediction:submit", "wallet:manage"]
        self.assertIs(ha._granted_scope_list(scopes), scopes)

    def test_dict_scopes_key_unwrapped(self):
        self.assertEqual(
            ha._granted_scope_list({"scopes": ["wallet:manage"]}),
            ["wallet:manage"],
        )

    def test_dict_granted_scopes_key_unwrapped(self):
        self.assertEqual(
            ha._granted_scope_list({"granted_scopes": ["credits:read"]}),
            ["credits:read"],
        )

    def test_unknown_shape_is_none_not_empty(self):
        self.assertIsNone(ha._granted_scope_list({"error": "shape drift"}))
        self.assertIsNone(ha._granted_scope_list(None))
        self.assertIsNone(ha._granted_scope_list({"scopes": "not-a-list"}))


class WalletSetupGuidanceTests(unittest.TestCase):
    def test_guidance_never_reads_owner_or_suggests_self_grant_or_purchase(self):
        for scopes in (None, [], ["wallet:manage"], ["wallet:read"], {"unexpected": []}):
            with mock.patch.object(ha, "authed") as http:
                message = ha._wallet_setup_guidance(scopes)
            http.assert_not_called()
            self.assertNotIn("scope --add", message)
            self.assertIn("Requests do not debit", message)
            self.assertIn("not a new purchase", message)
            self.assertIn("idempotency-key", message)


if __name__ == "__main__":
    unittest.main()
