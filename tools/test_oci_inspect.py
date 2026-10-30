import tempfile
import unittest
from pathlib import Path
from oci_handoff import publish
from oci_inspect import inspect

def record():
    digest = "sha256:" + "a" * 64
    return {"schemaVersion":"0.1","taskId":"task-1","sessionId":"session-1",
            "repository":{"url":"local:test","commit":"b"*40},
            "artifact":{"ociDigest":digest,"mediaType":"application/wasm"},"evidence":[]}

class TestInspector(unittest.TestCase):
    def test_round_trip_is_integrity_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            digest = publish(record(), tmp)
            result = inspect(tmp)
            self.assertEqual(result["artifacts"][0]["digest"], digest)
            self.assertEqual(result["artifacts"][0]["integrity"], "verified")
            self.assertEqual(result["artifacts"][0]["signature"], "not-verified")
            self.assertEqual(result["artifacts"][0]["publication"], "not-authorized")

    def test_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            digest = publish(record(), tmp)
            (Path(tmp) / "blobs" / "sha256" / digest[7:]).write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                inspect(tmp)

    def test_symlink_blob_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            digest = publish(record(), tmp)
            blob = Path(tmp) / "blobs" / "sha256" / digest[7:]
            backup = blob.with_suffix(".backup")
            blob.rename(backup)
            blob.symlink_to(backup)
            with self.assertRaises(ValueError):
                inspect(tmp)

if __name__ == "__main__":
    unittest.main()
