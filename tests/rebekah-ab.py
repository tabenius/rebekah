#!/usr/bin/env python3
"""Exercise deployment state transitions without touching a real service."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/podman/rebekah-ab"
A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64
C = "sha256:" + "c" * 64

STUB = '''#!/usr/bin/env python3
import json, os, pathlib, signal, sys
root = pathlib.Path(os.environ["TEST_ROOT"])
args = sys.argv[1:]
if pathlib.Path(sys.argv[0]).name == "systemctl":
    with (root / "restarts").open("a") as f: f.write("restart\\n")
    pin = next(line.split("=",1)[1] for line in (root / "rebekah.env").read_text().splitlines() if line.startswith("REBEKAH_IMAGE="))
    if os.environ.get("SIGNAL_ONCE") and not (root / "signalled").exists():
        (root / "signalled").touch()
        os.kill(os.getppid(), signal.SIGTERM)
        sys.exit(1)
    if os.environ.get("FAIL_PIN") == pin: sys.exit(1)
    if not os.environ.get("KEEP_OLD"): (root / "running").write_text(pin)
    sys.exit(0)
if args[0] == "image": print(args[-1]); sys.exit(0)
if args[0] == "inspect": print((root / "running").read_text()); sys.exit(0)
if args[0] == "run":
    pin = args[-2]
    contract = "v1.extra" if pin.endswith("c" * 64) else "v1"
    print("AGENDA_CONTRACTS = {" + repr(contract) + "}"); sys.exit(0)
if args[0] == "exec":
    if "rebekah-health" in args: sys.exit(0)
    if "jq" in args: print(json.load(sys.stdin)["status"]); sys.exit(0)
    if args[-1] == "/run/rebekah/gateway-token": print("test-token"); sys.exit(0)
    if args[-1] == "/usr/local/lib/rebekah/gateway.py": print("AGENDA_CONTRACTS = {'v1'}"); sys.exit(0)
    if "healthz" in args[-1]: print("200"); sys.exit(0)
    print(json.dumps({"status": os.environ.get("VIEW_STATUS", "ready"), "nested": {"status": "cancelled"}})); sys.exit(0)
sys.exit(2)
'''


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("podman", "systemctl"):
            stub = self.root / name
            stub.write_text(STUB)
            stub.chmod(0o755)
        (self.root / "rebekah.env").write_text(f"REBEKAH_NAME=test\nREBEKAH_IMAGE={A}\nOTHER=keep\n")
        (self.root / "running").write_text(A)
        self.env = dict(os.environ, TEST_ROOT=str(self.root),
                        PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                        REBEKAH_ENV_FILE=str(self.root / "rebekah.env"), AB_WAIT_SECONDS="1")
        for key in ("REBEKAH_NAME", "REBEKAH_UNIT", "REBEKAH_DAILY_AGENDA_HOST_DIR", "CONTAINER_RUNTIME"):
            self.env.pop(key, None)

    def run_ab(self, *args, code=0, **env):
        result = subprocess.run(["bash", str(SCRIPT), *args], env=dict(self.env, **env),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def agenda(self):
        directory = self.root / "agenda"
        directory.mkdir()
        (directory / "manifest.json").write_text(json.dumps({"generator_contract": "v1"}))
        with (self.root / "rebekah.env").open("a") as f:
            f.write(f"REBEKAH_DAILY_AGENDA_HOST_DIR={directory}\n")

    def test_dry_run_changes_nothing_including_b(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.run_ab("--dry-run", "activate", B)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_promotion_and_backup(self):
        self.run_ab("activate", B)
        self.assertEqual((self.root / "last-good-image").read_text().strip(), B)
        self.assertEqual((self.root / "previous-good-image").read_text().strip(), A)
        self.assertIn("OTHER=keep", (self.root / "rebekah.env").read_text())
        self.assertTrue(list(self.root.glob("rebekah.env.backup.*")))

    def test_restart_failure_rolls_back_without_promoting(self):
        self.run_ab("activate", B, code=1, FAIL_PIN=B)
        self.assertEqual((self.root / "running").read_text(), A)
        self.assertEqual((self.root / "last-good-image").read_text().strip(), A)

    def test_stale_container_cannot_be_promoted(self):
        self.run_ab("activate", B, code=1, KEEP_OLD="1")
        self.assertEqual((self.root / "last-good-image").read_text().strip(), A)

    def test_contract_match_is_exact_and_preflight_is_non_disruptive(self):
        self.agenda()
        before = (self.root / "rebekah.env").read_text()
        self.run_ab("activate", C, code=2)
        self.assertEqual((self.root / "rebekah.env").read_text(), before)
        self.assertFalse((self.root / "restarts").exists())

    def test_configured_agenda_cannot_report_not_configured(self):
        self.agenda()
        self.run_ab("verify", code=1, VIEW_STATUS="not-configured")

    def test_missing_manifest_is_failure(self):
        self.agenda()
        (self.root / "agenda/manifest.json").unlink()
        self.run_ab("activate", B, code=2)
        self.assertFalse((self.root / "restarts").exists())

    def test_mutable_or_injected_reference_is_refused(self):
        for pin in ("latest", "image:latest", B + "|oops", B + "\nOTHER=oops"):
            self.run_ab("activate", pin, code=2)
        self.assertFalse((self.root / "restarts").exists())

    def test_concurrent_activation_is_refused(self):
        with (self.root / "ab.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.run_ab("activate", B, code=2)
        self.assertFalse((self.root / "restarts").exists())

    def test_both_candidate_and_rollback_fail_honestly(self):
        self.agenda()
        (self.root / "last-good-image").write_text(A + "\n")
        result = self.run_ab("activate", B, code=3, VIEW_STATUS="unavailable")
        self.assertIn("BOTH A and B failed", result.stderr)
        self.assertEqual((self.root / "running").read_text(), A)
        self.assertEqual((self.root / "last-good-image").read_text().strip(), A)

    def test_no_verified_baseline_refuses_activation(self):
        self.agenda()
        self.run_ab("activate", B, code=2, VIEW_STATUS="unavailable")
        self.assertFalse((self.root / "restarts").exists())

    def test_pending_transaction_blocks_activation_and_recovers(self):
        (self.root / "pending-rollback-image").write_text(A + "\n")
        (self.root / "running").write_text(B)
        self.run_ab("activate", B, code=2)
        self.assertFalse((self.root / "restarts").exists())
        self.run_ab("recover")
        self.assertEqual((self.root / "running").read_text(), A)
        self.assertFalse((self.root / "pending-rollback-image").exists())

    def test_failed_recovery_keeps_journal(self):
        self.agenda()
        (self.root / "pending-rollback-image").write_text(A + "\n")
        self.run_ab("recover", code=3, VIEW_STATUS="unavailable")
        self.assertTrue((self.root / "pending-rollback-image").exists())

    def test_malformed_config_fails_before_any_mutation(self):
        (self.root / "rebekah.env").write_text('REBEKAH_NAME="unterminated\n')
        self.run_ab("activate", B, code=2)
        self.assertFalse((self.root / "restarts").exists())

    def test_term_during_activation_restores_baseline(self):
        self.run_ab("activate", B, code=143, SIGNAL_ONCE="1")
        self.assertEqual((self.root / "running").read_text(), A)
        self.assertEqual((self.root / "last-good-image").read_text().strip(), A)
        self.assertFalse((self.root / "pending-rollback-image").exists())


if __name__ == "__main__":
    unittest.main()
