import argparse,contextlib,io,json,unittest
from unittest import mock
import ha
from ha_client.errors import HAFailure

class DefaultPredictionScopesTests(unittest.TestCase):
    def test_no_args_and_explicit_all_use_bulk_prediction_subscription_only(self):
        for explicit in (False,True):
            output=io.StringIO()
            with mock.patch.object(ha,'authed',return_value=(200,{'mode':'all','scopes':['GC','BTC'],'excluded':[]})) as send,contextlib.redirect_stdout(output):
                ha.cmd_subscribe(argparse.Namespace(scope=[],all=explicit))
            send.assert_called_once_with('POST','/agent/prediction-scope')
            self.assertEqual(json.loads(output.getvalue())['mode'],'all')
    def test_individual_selection_keeps_specific_subscription_path(self):
        with mock.patch.object(ha,'authed',return_value=(204,{})) as send,contextlib.redirect_stdout(io.StringIO()):
            ha.cmd_subscribe(argparse.Namespace(scope=['GC'],all=False))
        send.assert_called_once_with('POST','/agent/prediction-scope/GC')
    def test_all_with_individual_keys_is_rejected_before_request(self):
        with mock.patch.object(ha,'authed') as send,self.assertRaises(HAFailure),contextlib.redirect_stderr(io.StringIO()):
            ha.cmd_subscribe(argparse.Namespace(scope=['GC'],all=True))
        send.assert_not_called()
