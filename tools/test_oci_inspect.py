import tempfile
import json
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

    def test_subject_status_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            publish(record(), tmp)
            self.assertEqual(inspect(tmp)["artifacts"][0]["subjectStatus"], "none")

    def test_unresolved_subject_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            publish(record(), tmp)
            root = Path(tmp)
            index_path = root / "index.json"
            index = json.loads(index_path.read_text())
            descriptor = index["manifests"][0]
            manifest_path = root / "blobs" / "sha256" / descriptor["digest"][7:]
            manifest = json.loads(manifest_path.read_text())
            manifest["subject"] = {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                                   "digest": "sha256:" + "f" * 64, "size": 999}
            self._replace_manifest(root, index, manifest)
            self.assertEqual(inspect(root)["artifacts"][0]["subjectStatus"], "unresolved")

    @staticmethod
    def _replace_manifest(root, index, manifest):
        import hashlib
        encoded = json.dumps(manifest, separators=(",", ":")).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        (root / "blobs" / "sha256" / digest).write_bytes(encoded)
        index["manifests"][0]["digest"] = "sha256:" + digest
        index["manifests"][0]["size"] = len(encoded)
        (root / "index.json").write_text(json.dumps(index))

    def test_local_subject_association(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_digest = publish(record(), root)
            index_path = root / "index.json"
            index = json.loads(index_path.read_text())
            original = dict(index["manifests"][0])
            base_path = root / "blobs" / "sha256" / original_digest[7:]
            manifest = json.loads(base_path.read_text())
            manifest["subject"] = {key: original[key] for key in ("mediaType", "digest", "size")}
            self._replace_manifest(root, index, manifest)
            index["manifests"].append(original)
            index_path.write_text(json.dumps(index))
            result = inspect(root)
            by_digest = {entry["digest"]: entry for entry in result["artifacts"]}
            updated = index["manifests"][0]["digest"]
            self.assertEqual(by_digest[updated]["subjectStatus"], "verified-local-association")
            self.assertEqual(by_digest[updated]["signature"], "not-verified")
            self.assertEqual(by_digest[original_digest]["referrers"], [updated])
            self.assertEqual(by_digest[updated]["referrers"], [])

    def test_duplicate_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            publish(record(), tmp)
            index_path = Path(tmp) / "index.json"
            index = json.loads(index_path.read_text())
            index["manifests"].append(dict(index["manifests"][0]))
            index_path.write_text(json.dumps(index))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                inspect(tmp)

    def test_invalid_subject_descriptor_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            publish(record(), root)
            index_path = root / "index.json"
            index = json.loads(index_path.read_text())
            manifest_path = root / "blobs" / "sha256" / index["manifests"][0]["digest"][7:]
            manifest = json.loads(manifest_path.read_text())
            manifest["subject"] = {"digest": "sha256:" + "f" * 64, "size": "invalid"}
            self._replace_manifest(root, index, manifest)
            with self.assertRaisesRegex(ValueError, "subject descriptor"):
                inspect(root)

    def test_symlink_blob_directory_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            publish(record(), tmp)
            root = Path(tmp)
            directory = root / "blobs" / "sha256"
            backup = root / "original-blobs"
            directory.rename(backup)
            directory.symlink_to(backup, target_is_directory=True)
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
