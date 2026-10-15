"""Civic CLI context transport tests. All network and credential access is mocked."""
import argparse
import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ha
from test_ha_forecast_contract import NUMERIC_ITEM, BINARY_ITEM, ORDERED_ITEM, contract_entry

CONTEXT = {'schema_version': '1', 'decision': 'initial',
           'observed_at': '2026-10-15T00:00:00Z', 'run_ref': 'synthetic-owned-run'}


class CivicForecastContextTests(unittest.TestCase):
    def args(self, item=NUMERIC_ITEM, **overrides):
        values = dict(challenge_id=item['id'], mean=None, std=None, samples=None,
                      yes_probability=None, probability=None, amount=25.0,
                      rationale='Synthetic test only', expected_revision=None,
                      idempotency_key='synthetic-stable-key', bin=None, bin_label=None)
        if item['outcome_shape'] == 'numeric_distribution':
            values.update(mean=3.4, std=0.2)
        elif item['outcome_shape'] == 'binary_probability':
            values['yes_probability'] = 0.6
        else:
            values['probability'] = ['down=0.2', 'flat=0.3', 'up=0.5']
        values.update(overrides)
        return argparse.Namespace(**values)

    def submit(self, args, item=NUMERIC_ITEM, response=None, legacy=False):
        entry = copy.deepcopy(contract_entry(item))
        if legacy:
            entry['contract'].update(execution_family='macro_numeric',
                                     submission_route='macro_numeric_legacy',
                                     compatibility_status='legacy_open_round')
        with mock.patch.object(ha, '_fetch_prediction_contract_entries', return_value=(200, [entry])), \
             mock.patch.object(ha, 'authed', return_value=(201, response or {})) as call, \
             mock.patch.object(ha, 'out') as output:
            ha.cmd_forecast(args)
        return call, output

    def test_context_is_forwarded_for_all_native_shapes(self):
        for item in (NUMERIC_ITEM, BINARY_ITEM, ORDERED_ITEM):
            with self.subTest(shape=item['outcome_shape']):
                baseline, _ = self.submit(self.args(item), item)
                call, _ = self.submit(self.args(item, forecast_context=json.dumps(CONTEXT)), item)
                body = dict(call.call_args.args[2])
                self.assertEqual(body.pop('forecast_context'), CONTEXT)
                self.assertEqual(body, baseline.call_args.args[2])
                self.assertEqual(call.call_args.args[:2],
                                 ('POST', '/eval/human-forecasts/challenges/'+item['id']+'/forecast'))
                self.assertEqual(call.call_count, 1)

    def test_samples_and_context_do_not_change_distribution(self):
        args = self.args(mean=None, std=None, samples=','.join(str(i) for i in range(10)),
                         forecast_context=json.dumps(CONTEXT))
        call, _ = self.submit(args)
        self.assertEqual(len(call.call_args.args[2]['forecast']['samples']), 10)
        self.assertEqual(call.call_args.args[2]['forecast_context'], CONTEXT)

    def test_no_flag_preserves_old_payload_and_generated_idempotency_key(self):
        old, _ = self.submit(self.args(idempotency_key=None))
        omitted, _ = self.submit(self.args(idempotency_key=None, forecast_context=None))
        with_context, _ = self.submit(self.args(idempotency_key=None, forecast_context=CONTEXT))
        self.assertNotIn('forecast_context', old.call_args.args[2])
        self.assertEqual(old.call_args.args, omitted.call_args.args)
        self.assertEqual(old.call_args.args[2]['idempotency_key'],
                         with_context.call_args.args[2]['idempotency_key'])

    def test_file_context_and_revision_are_preserved(self):
        context = dict(CONTEXT, decision='update')
        with tempfile.NamedTemporaryFile() as file:
            file.write(json.dumps(context).encode()); file.flush()
            call, _ = self.submit(self.args(forecast_context='@'+file.name, expected_revision=3))
        self.assertEqual(call.call_args.args[2]['forecast_context'], context)
        self.assertEqual(call.call_args.args[2]['expected_revision'], 3)

    def test_frozen_legacy_macro_route_keeps_native_body(self):
        baseline, _ = self.submit(self.args(), legacy=True)
        call, _ = self.submit(self.args(forecast_context=CONTEXT), legacy=True)
        body = dict(call.call_args.args[2])
        self.assertEqual(body.pop('forecast_context'), CONTEXT)
        self.assertEqual(body, baseline.call_args.args[2])
        self.assertEqual(call.call_args.args[:2], ('POST', '/eval/macro/challenges/c-numeric/predict'))
        self.assertNotIn('forecast', body)
        self.assertNotIn('idempotency_key', body)

    def test_invalid_metadata_fails_before_discovery_or_stake(self):
        for context in ({'decision': 'hold'}, {'decision': 'abstain'},
                        {'verified': True}, {'prompt': 'not allowed'},
                        {'run_ref': 'x'*32769}, 'not-json', '[]'):
            with self.subTest(context_type=type(context).__name__), \
                 mock.patch.object(ha, '_fetch_prediction_contract_entries') as discover, \
                 mock.patch.object(ha, 'authed') as send:
                with self.assertRaises(ha.HAFailure):
                    ha.cmd_forecast(self.args(forecast_context=context))
                discover.assert_not_called(); send.assert_not_called()

    def test_missing_context_receipt_does_not_trigger_an_extra_prediction(self):
        response = {'forecast_id': 'synthetic-id', 'revision_number': 1}
        call, output = self.submit(self.args(forecast_context=CONTEXT), response=response)
        self.assertEqual(call.call_count, 1)
        output.assert_called_once_with(response)
        self.assertNotIn('context_receipt_id', response)

    def test_cli_accepts_context_flag_without_network(self):
        argv = ['ha.py', 'forecast', 'synthetic-id', '--mean', '3.4', '--std', '0.2',
                '--amount', '25', '--forecast-context', '@synthetic-context.json']
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(ha, 'check_for_update'), \
             mock.patch.object(ha, 'cmd_forecast') as handler:
            ha.main()
        self.assertEqual(handler.call_args.args[0].forecast_context, '@synthetic-context.json')


if __name__ == '__main__':
    unittest.main()
