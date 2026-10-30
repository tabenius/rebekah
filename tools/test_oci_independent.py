import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from oci_inspect import inspect

FIXTURE = Path(__file__).with_name("oci_fixture.mjs")

@unittest.skipUnless(shutil.which("node"), "Node.js is required for independent fixture")
class TestIndependentOciFixture(unittest.TestCase):
    def test_node_generated_layout_inspected_by_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            generated = subprocess.run(
                ["node", str(FIXTURE), tmp], check=True,
                capture_output=True, text=True
            )
            expected = json.loads(generated.stdout)
            report = inspect(tmp)
            by_digest = {item["digest"]: item for item in report["artifacts"]}
            self.assertEqual(len(by_digest), 2)
            self.assertEqual(by_digest[expected["base"]]["referrers"], [expected["referrer"]])
            self.assertEqual(by_digest[expected["referrer"]]["subjectStatus"], "verified-local-association")
            for artifact in report["artifacts"]:
                self.assertEqual(artifact["integrity"], "verified")
                self.assertEqual(artifact["publication"], "not-authorized")

    def test_independent_fixture_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            generated = subprocess.run(
                ["node", str(FIXTURE), tmp], check=True,
                capture_output=True, text=True
            )
            digest = json.loads(generated.stdout)["base"]
            blob = Path(tmp) / "blobs" / "sha256" / digest[7:]
            blob.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "size mismatch|digest mismatch"):
                inspect(tmp)
