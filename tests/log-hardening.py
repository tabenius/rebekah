#!/usr/bin/env python3
"""Logging and idle-poll regressions; no production endpoints or containers."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'gateway', os.environ.get('REBEKAH_GATEWAY_SOURCE', ROOT / 'nix/gateway.py'))
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)


class LoggingTests(unittest.TestCase):
    def test_protocol_diagnostics_never_echo_request_secrets(self):
        handler_class = gw.make_handler(None, None)
        handler = handler_class.__new__(handler_class)
        with patch.object(gw.sys, 'stderr', new_callable=io.StringIO) as output:
            handler.log_message('bad request %s', '/?token=secret\nforged=event')
        text = output.getvalue()
        self.assertIn('level=WARN event=http_protocol_diagnostic', text)
        self.assertNotIn('secret', text)
        self.assertEqual(len(text.splitlines()), 1)

    def test_gateway_log_is_one_line_and_preserves_level(self):
        with patch.object(gw.sys, 'stderr', new_callable=io.StringIO) as output:
            gw.gateway_log('ERROR', 'upstream_failure', status=503, message='failed\nretry')
        self.assertIn('level=ERROR', output.getvalue())
        self.assertIn('status=503', output.getvalue())
        self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_http_response_severity_and_silent_success(self):
        handler_class = gw.make_handler(None, None)
        handler = handler_class.__new__(handler_class)
        handler.send_response_only = lambda *args: None
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        for status, expected in [(200, None), (302, None), (404, 'WARN'), (503, 'ERROR')]:
            with self.subTest(status=status):
                with patch.object(gw.sys, 'stderr', new_callable=io.StringIO) as output:
                    handler._write_head(status, [], 0)
                if expected is None:
                    self.assertEqual(output.getvalue(), '')
                else:
                    self.assertIn('level=' + expected, output.getvalue())
                    self.assertIn('status=' + str(status), output.getvalue())

    def test_idle_kanban_cache_does_not_slow_oversight(self):
        now = [100]
        cfg = SimpleNamespace(dash_url='http://127.0.0.1', dash_push_key='test', dash_poll=30)
        pusher = gw.DashPusher(cfg, None, clock=lambda: now[0])
        names = ['v1_system', 'v1_attention', 'v1_changesets', 'v1_daily_agenda',
                 'v1_change_requests', 'v1_frog_kanban']
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name in names:
                stack.enter_context(patch.object(gw, name, return_value={}))
            fetch = stack.enter_context(patch.object(gw, 'fetch_kanban', return_value={'cards': []}))
            oversight = stack.enter_context(patch.object(gw, 'v1_oversight', return_value={}))
            for offset in [0, 5, 10, 15, 20, 25]:
                now[0] = 100 + offset
                pusher.views()
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(oversight.call_count, 6)
            now[0] = 130
            pusher.views()
            self.assertEqual(fetch.call_count, 2)
            pusher.refresh = True
            now[0] = 131
            pusher.views()
            self.assertEqual(fetch.call_count, 3)
            pusher.refresh = False
            now[0] = 161
            fetch.return_value = None
            pusher.views()
            now[0] = 166
            pusher.views()
            self.assertEqual(fetch.call_count, 5, 'failed reads must not be cached as healthy')

    def test_launcher_selects_one_explicit_log_driver(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            podman = root / 'podman'
            podman.write_text('#!/usr/bin/python3\nimport json,os,sys\n'
                              'args=sys.argv[1:]\n'
                              'if args[:2]==["secret","exists"]: sys.exit(1)\n'
                              'if args[0]=="run": open(os.environ["CAPTURE"],"w").write(json.dumps(args))\n')
            podman.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'],
                       REBEKAH_IMAGE='sha256:' + 'a' * 64, REBEKAH_WORKSPACE=str(ROOT),
                       REBEKAH_ENV_FILE=str(root / 'missing'), REBEKAH_DAILY_AGENDA_HOST_DIR='',
                       REBEKAH_RUNTIME_STATUS_DIR='', CAPTURE=str(root / 'args.json'))
            env['INVOCATION_ID'] = ''
            for driver in ['journald', 'none']:
                env['REBEKAH_LOG_DRIVER'] = driver
                subprocess.run(['bash', str(ROOT / 'deploy/podman/rebekah-run')], env=env, check=True, capture_output=True)
                args = json.loads((root / 'args.json').read_text())
                self.assertEqual(args.count('--log-driver'), 1)
                self.assertEqual(args[args.index('--log-driver') + 1], driver)
            env['REBEKAH_LOG_DRIVER'] = 'invalid'
            result = subprocess.run(['bash', str(ROOT / 'deploy/podman/rebekah-run')], env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)

            cat = root / 'systemd-cat'
            cat.write_text('#!/usr/bin/python3\nimport json,os,sys\n'
                           'args=sys.argv[1:]\n'
                           'open(os.environ["CAT_CAPTURE"],"w").write(json.dumps(args[:3]))\n'
                           'os.execvp(args[3], args[3:])\n')
            cat.chmod(0o755)
            env.update(INVOCATION_ID='synthetic-unit-invocation', REBEKAH_LOG_DRIVER='none',
                       REBEKAH_NAME='test-instance', CAT_CAPTURE=str(root / 'cat-args.json'))
            subprocess.run(['bash', str(ROOT / 'deploy/podman/rebekah-run')], env=env, check=True, capture_output=True)
            self.assertEqual(json.loads((root / 'cat-args.json').read_text()),
                             ['--identifier=test-instance', '--priority=info', '--stderr-priority=err'])


if __name__ == '__main__':
    unittest.main()
