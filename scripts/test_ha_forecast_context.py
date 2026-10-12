import argparse
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(__file__))
import ha
from ha_client.prediction import parse_forecast_context, build_unified_predict_body
from ha_client.errors import HAFailure

P={'bearish': .2, 'neutral': .3, 'bullish': .5}

class OptionalContextTests(unittest.TestCase):
    def args(self, **kw):
        return argparse.Namespace(**dict(challenge_id='fixture', direction=None, confidence=None,
            probabilities=json.dumps(P), reasoning='Synthetic rationale with sufficient length', summary=None,
            revision=False, amount=None, **kw))

    def test_old_payload_has_no_new_fields(self):
        with mock.patch.object(ha,'authed',return_value=(201,{})) as call, mock.patch.object(ha,'out'):
            ha.cmd_predict(self.args())
        self.assertEqual(call.call_args.args[2],{'probabilities':P,'reasoning':'Synthetic rationale with sufficient length','is_revision':False})

    def test_hold_transmits_exact_vector_revision_and_optional_context(self):
        args=self.args(forecast_context='{"decision":"hold","run_ref":"owned-run"}', expected_revision=2)
        with mock.patch.object(ha,'authed',return_value=(200,{'status':'held'})) as call, mock.patch.object(ha,'out'):
            ha.cmd_predict(args)
        body=call.call_args.args[2]
        self.assertEqual(body['forecast_context']['decision'],'hold')
        self.assertEqual(body['expected_revision'],2); self.assertEqual(body['probabilities'],P)

    def test_abstain_sdk_omits_prediction_and_stake(self):
        body=build_unified_predict_body('c',forecast_context={'decision':'abstain'},expected_revision=2)
        self.assertNotIn('prediction',body); self.assertNotIn('amount',body)
        with self.assertRaises(HAFailure):
            build_unified_predict_body('c',forecast_context={'decision':'abstain'})

    def test_bounded_file_and_no_trust_claims(self):
        with tempfile.NamedTemporaryFile() as file:
            file.write(b'{"run_ref":"run-1"}'); file.flush()
            self.assertEqual(parse_forecast_context('@'+file.name),{'run_ref':'run-1'})
        for value in ({'verified':True},{'training_eligible':True},{'run_ref':'a'*32769}):
            with self.assertRaises(HAFailure): parse_forecast_context(value)

    def test_readback_is_authenticated_and_version_filtered(self):
        args=argparse.Namespace(challenge_id='fixture',limit=20,revision_number=2,cursor=None)
        with mock.patch.object(ha,'authed',return_value=(200,{'receipts':[]})) as call, mock.patch.object(ha,'out'):
            ha.cmd_forecast_contexts(args)
        self.assertEqual(call.call_args.args,('GET','/eval/challenges/fixture/forecast-contexts?limit=20&revision_number=2'))

if __name__=='__main__': unittest.main()
