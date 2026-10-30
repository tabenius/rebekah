import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from oci_handoff import publish, verify

def record():
    d = "sha256:" + "a" * 64
    return {"schemaVersion":"0.1","taskId":"task-1","sessionId":"session-1","repository":{"url":"local:test","commit":"b"*40},"artifact":{"ociDigest":d,"mediaType":"application/wasm"},"evidence":[{"kind":"test","digest":d,"source":"local:test-run"}]}

class TestOciHandoff(unittest.TestCase):
    def test_round_trip_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            digest = publish(record(), tmp)
            self.assertEqual(verify(tmp), record())
            self.assertEqual(publish(record(), tmp), digest)

    def test_corrupt_blob_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            digest = publish(record(), tmp)
            path = Path(tmp) / "blobs" / "sha256" / digest[7:]
            path.write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                verify(tmp)

    def test_preserves_other_index_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            publish(record(), tmp)
            index_path = Path(tmp) / "index.json"
            index = json.loads(index_path.read_text())
            other = {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                     "digest": "sha256:" + "c" * 64, "size": 123}
            index["manifests"].append(other)
            index_path.write_text(json.dumps(index))
            publish(record(), tmp)
            after = json.loads(index_path.read_text())
            self.assertEqual(len(after["manifests"]), 2)
            self.assertIn(other, after["manifests"])
            self.assertEqual(verify(tmp), record())

    def test_invalid_container_types_rejected(self):
        for field, value in (("repository", []), ("artifact", []), ("evidence", [None])):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                candidate = record()
                candidate[field] = value
                with self.assertRaises(ValueError):
                    publish(candidate, tmp)

    def test_invalid_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = record()
            value["evidence"][0]["digest"] = "not-a-digest"
            with self.assertRaises(ValueError):
                publish(value, tmp)

if __name__ == "__main__":
    unittest.main()
