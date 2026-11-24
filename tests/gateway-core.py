#!/usr/bin/env python3
"""Stdlib-only core helpers and installed-layout import smoke."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gateway", ROOT / "nix/gateway.py")
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)


class CoreTests(unittest.TestCase):
    def test_authentication_syntax_does_not_grant_identity(self):
        for value in (None, "", "Basic abc", "Bearer "):
            self.assertIsNone(gw._core.bearer_credential(value))
        self.assertEqual(gw._core.bearer_credential("bEaReR  opaque "), "opaque")
        cfg = gw.Config({"REBEKAH_GATEWAY_EXPOSE": "frog", "REBEKAH_AUTH_PASSWORD": "0"})
        self.assertIsNone(gw.Authenticator(cfg).principal("Bearer opaque"))

    def test_validation_remains_noncoercing(self):
        for value in (True, "2", -1, 100001):
            with self.assertRaises(ValueError):
                gw._agenda_count(value)
        self.assertEqual(gw._agenda_count(0), 0)
        with self.assertRaises(ValueError):
            gw._agenda_time("2026-11-24T00:00:00")
        self.assertEqual(gw._agenda_text("abcdef", 3), "abc")

    def test_installed_layout_works_without_site_packages_or_cwd_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            shutil.copy2(ROOT / "nix/gateway.py", path / "gateway.py")
            shutil.copytree(ROOT / "nix/gateway_core", path / "gateway_core", ignore=shutil.ignore_patterns("__pycache__"))
            result = subprocess.run([sys.executable, "-S", "-c", f"import runpy; runpy.run_path({str(path / 'gateway.py')!r})"],
                                    cwd="/", capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("${./gateway_core}", (ROOT / "nix/image.nix").read_text())


if __name__ == "__main__":
    unittest.main()
