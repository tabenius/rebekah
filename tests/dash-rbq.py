#!/usr/bin/env python3
"""RBQ transport integration without exposing a credential to the gateway."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gateway', ROOT/'nix/gateway.py')
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)


def config(**extra):
    env = dict(REBEKAH_GATEWAY_TOKEN='test-gateway-secret', REBEKAH_AUTH_PASSWORD='0',
               REBEKAH_DASH_TRANSPORT='rbq', REBEKAH_DASH_URL='https://dash.ragbaz.cc',
               REBEKAH_DASH_CREDENTIAL_STORE='/var/lib/rebekah/gateway/rbq-credentials',
               REBEKAH_DASH_INSTANCE_ID='test-instance',
               REBEKAH_DASH_CREDENTIAL_SEAL='none')
    env.update(extra)
    return gw.Config(env)


class RbqTransport(unittest.TestCase):
    def test_pairing_helper_requests_only_connector_and_requires_explicit_sealing(self):
        helper_spec = importlib.util.spec_from_file_location('pair', ROOT/'nix/dash-pair.py')
        pair = importlib.util.module_from_spec(helper_spec); helper_spec.loader.exec_module(pair)
        args = SimpleNamespace(credential_store='/var/lib/rebekah/gateway/rbq-credentials',
            rbq='/usr/local/bin/rbq', timeout=0, seal='none', dash='https://dash.ragbaz.cc', label='canary')
        command = pair.command(args)
        self.assertEqual(command[command.index('--scope')+1], 'connector')
        self.assertEqual(command[command.index('--seal')+1], 'none')
        self.assertNotIn('approve', command)

    def test_explicit_mode_has_one_primary_and_keeps_manual_mirrors_separate(self):
        cfg = config(REBEKAH_DASH_PUSH_KEY='old-private-key')
        self.assertEqual(cfg.validate(), [])
        self.assertEqual(cfg.dash_push_key, '')
        self.assertEqual(cfg.dash_targets, [('https://dash.ragbaz.cc', '')])
        primary = gw.DashPusher(cfg, None, target=cfg.dash_targets[0])
        mirror = gw.DashPusher(cfg, None, target=('http://localhost:8000', 'key'), commands=False)
        self.assertTrue(primary._rbq)
        self.assertFalse(mirror._rbq)

    def test_bad_or_ambiguous_configuration_fails_closed(self):
        for values in [dict(REBEKAH_DASH_TRANSPORT='unknown'), dict(REBEKAH_DASH_CREDENTIAL_STORE='relative'),
                       dict(REBEKAH_DASH_CREDENTIAL_SEAL='fallback'), dict(REBEKAH_DASH_CREDENTIAL_ID='not-id'),
                       dict(REBEKAH_DASH_URL='https://dash.ragbaz.cc/path'), dict(REBEKAH_RBQ='rbq'),
                       dict(REBEKAH_DASH_TRANSPORT='push-key')]:
            self.assertTrue(config(**values).validate(), values)

    def test_delegates_only_allowed_requests_and_never_passes_gateway_secrets(self):
        cfg = config(); pusher = gw.DashPusher(cfg, None)
        result = SimpleNamespace(returncode=0, stdout=json.dumps({'status': 200, 'body': {'ok': True}, 'retry_after': None}).encode())
        with patch.dict(os.environ, {'REBEKAH_GATEWAY_TOKEN': 'private', 'REBEKAH_EPHOR_OVERSIGHT_TOKEN': 'also-private'}), patch.object(subprocess, 'run', return_value=result) as run:
            self.assertEqual(pusher._call('POST', '/api/connector/push', {'views': {}}), (200, {'ok': True}, None))
            args = run.call_args.args[0]
            self.assertEqual(args[-1], 'push')
            self.assertIn('--credential-store', args)
            self.assertIn('--seal', args)
            self.assertIn('--dash', args)
            self.assertNotIn('private', args)
            self.assertNotIn('REBEKAH_GATEWAY_TOKEN', run.call_args.kwargs['env'])
            self.assertNotIn('REBEKAH_EPHOR_OVERSIGHT_TOKEN', run.call_args.kwargs['env'])
            self.assertEqual(json.loads(run.call_args.kwargs['input']), {'views': {}})
            run.reset_mock()
            with self.assertRaises(OSError): pusher._call('POST', '/arbitrary/path', {})
            run.assert_not_called()

    def test_malformed_large_failed_and_timed_out_results_do_not_leak_details(self):
        pusher = gw.DashPusher(config(), None)
        for result in [SimpleNamespace(returncode=1, stdout=b'secret'),
                       SimpleNamespace(returncode=0, stdout=b'secret'),
                       SimpleNamespace(returncode=0, stdout=b'x'*(gw.DASH_MAX_RESPONSE*2+1)),
                       SimpleNamespace(returncode=0, stdout=b'{"status":true}'),
                       SimpleNamespace(returncode=0, stdout=b'{"status":200,"retry_after":-1}')]:
            with patch.object(subprocess, 'run', return_value=result), self.assertRaises(OSError) as error:
                pusher._call('GET', '/api/connector/pending')
            self.assertNotIn('secret', str(error.exception))
        with patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('private', 30)), self.assertRaises(OSError):
            pusher._call('GET', '/api/connector/pending')


if __name__ == '__main__': unittest.main()
