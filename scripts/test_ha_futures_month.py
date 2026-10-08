import argparse
import unittest
from unittest.mock import patch
import ha


class FuturesMonthTests(unittest.TestCase):
    def discover(self, rows):
        args = argparse.Namespace(track="financial", public=True, asset=None, status="open", include_post_close=False)
        with patch.object(ha, '_fetch_financial_challenges', return_value=rows), patch.object(ha, 'creds', return_value={}), patch.object(ha, 'out') as output:
            ha.cmd_challenges(args)
        return output.call_args[0][0]['items']

    def test_explains_and_preserves_frozen_contract(self):
        row = self.discover([{'id': 'gold', 'asset': 'GC', 'contract_symbol': 'GCZ6', 'contract_month': '2026-12'}])[0]
        self.assertEqual(row['contract_month'], '2026-12')
        self.assertEqual(row['contract_label'], 'December 2026 (GCZ6)')

    def test_does_not_invent_month_for_spot_or_legacy(self):
        for row in self.discover([{'id': 'btc', 'asset': 'BTC'}, {'id': 'gc', 'asset': 'GC', 'contract_month': None}]):
            self.assertNotIn('contract_label', row)


if __name__ == '__main__':
    unittest.main()
