"""Owner-wallet CLI must not self-authorize or duplicate an uncertain debit."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import ha


class WalletSecurityTests(unittest.TestCase):
    def test_default_requests_without_debit(self):
        args = SimpleNamespace(amount="12.50", idempotency_key="wallet-request-001", auto=False)
        with patch.object(ha, "authed", return_value=(200, {"status": "pending"})) as http, patch.object(ha, "out"):
            ha.cmd_owner_topup(args)
        http.assert_called_once_with("POST", "/agent/owner/topup-requests", {"amount": "12.50", "idempotency_key": "wallet-request-001"})

    def test_auto_keeps_same_key_and_decimal_amount(self):
        args = SimpleNamespace(amount="12.50", idempotency_key="wallet-auto-001", auto=True)
        with patch.object(ha, "authed", return_value=(200, {"status": "completed"})) as http, patch.object(ha, "out"):
            ha.cmd_owner_topup(args)
            ha.cmd_owner_topup(args)
        self.assertEqual(http.call_args_list[0], http.call_args_list[1])
        self.assertEqual(http.call_args.args[1], "/agent/owner/topup")

    def test_policy_write_fails_before_network(self):
        with patch.object(ha, "authed") as http, self.assertRaises(ha.HAFailure):
            ha.cmd_wallet_policy(SimpleNamespace(max_balance=100, per_tx_limit=None))
        http.assert_not_called()

    def test_owner_scope_self_grant_fails_before_network(self):
        for scope in ("wallet:manage", "wallet:read", "wallet:topup"):
            with patch.object(ha, "authed") as http, self.assertRaises(ha.HAFailure):
                ha.cmd_scope(SimpleNamespace(add=[scope], remove=None, list=False))
            http.assert_not_called()

    def test_owner_balance_missing_permission_does_not_report_zero(self):
        with patch.object(ha, "authed", return_value=(403, {})), patch.object(ha, "out") as output, self.assertRaises(ha.HAFailure):
            ha.cmd_owner_balance(SimpleNamespace())
        output.assert_not_called()


if __name__ == '__main__':
    unittest.main()
