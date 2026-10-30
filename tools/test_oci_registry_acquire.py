#!/usr/bin/env python3
"""Offline, mocked tests for bounded OCI registry acquisition."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oci_registry_acquire import acquire, MAX_BLOB, MAX_TOTAL, MANIFEST_TYPE

def blob(value):
    data = json.dumps(value, separators=(",", ":")).encode()
    return data, "sha256:" + hashlib.sha256(data).hexdigest()

class Response:
    def __init__(self, payload, status=200, length=None):
        self.payload, self.status, self.length = payload, status, length
    def getheader(self, name):
        return str(self.length) if name == "Content-Length" and self.length is not None else None
    def read(self, size):
        return self.payload[:size]

class Connection:
    def __init__(self, response):
        self.response = response
        self.closed = False
    def request(self, method, path, headers=None):
        assert method == "GET"
        assert path.startswith("/v2/example/blobs/sha256:")
    def getresponse(self):
        return self.response
    def close(self):
        self.closed = True

class AcquisitionTests(unittest.TestCase):
    def fixture(self):
        payload, pdigest = blob({"payload": "test"})
        config, cdigest = blob({})
        manifest = {"schemaVersion": 2, "mediaType": MANIFEST_TYPE,
                    "config": {"digest": cdigest, "size": len(config), "mediaType": "application/vnd.oci.empty.v1+json"},
                    "layers": [{"digest": pdigest, "size": len(payload), "mediaType": "application/vnd.example.layer"}]}
        data, mdigest = blob(manifest)
        return data, mdigest, {cdigest: config, pdigest: payload}

    def run_acquire(self, data, digest, payloads, output, status=200):
        def connection(registry, repository, requested):
            return Connection(Response(payloads[requested], status=status, length=len(payloads[requested])))
        with patch("oci_registry_acquire.fetch_manifest", return_value=(data, MANIFEST_TYPE)), \
             patch("oci_registry_acquire._connection", side_effect=connection):
            return acquire("https://example.org", "example", digest, output)

    def test_acquires_verified_layout(self):
        data, digest, payloads = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "layout"
            result = self.run_acquire(data, digest, payloads, output)
            self.assertEqual(result["manifestDigest"], digest)
            self.assertEqual(result["publication"], "not-authorized")
            self.assertEqual((output / "blobs/sha256" / digest[7:]).read_bytes(), data)
            self.assertEqual(json.loads((output / "index.json").read_text())["manifests"][0]["digest"], digest)

    def test_rejects_tampered_blob_without_creating_layout(self):
        data, digest, payloads = self.fixture()
        key = next(k for k, value in payloads.items() if len(value) > 2)
        payloads[key] = b"x" * len(payloads[key])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "layout"
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                self.run_acquire(data, digest, payloads, output)
            self.assertFalse(output.exists())

    def test_rejects_redirect_without_creating_layout(self):
        data, digest, payloads = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "layout"
            with self.assertRaisesRegex(ValueError, "HTTP 302"):
                self.run_acquire(data, digest, payloads, output, status=302)
            self.assertFalse(output.exists())

    def test_write_failure_does_not_publish_partial_layout(self):
        data, digest, payloads = self.fixture()
        original_write = Path.write_bytes
        def fail_blob_write(path, value):
            if path.parent.name == "sha256":
                raise OSError("simulated disk failure")
            return original_write(path, value)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "layout"
            with patch.object(Path, "write_bytes", fail_blob_write):
                with self.assertRaisesRegex(OSError, "simulated disk failure"):
                    self.run_acquire(data, digest, payloads, output)
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(directory).iterdir()), [], "staging directory must be cleaned")

    def test_rejects_blob_content_length_mismatch(self):
        data, digest, payloads = self.fixture()
        def connection(registry, repository, requested):
            payload = payloads[requested]
            return Connection(Response(payload, length=len(payload) + 1))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "layout"
            with patch("oci_registry_acquire.fetch_manifest", return_value=(data, MANIFEST_TYPE)), \
                 patch("oci_registry_acquire._connection", side_effect=connection):
                with self.assertRaisesRegex(ValueError, "Content-Length mismatch"):
                    acquire("https://example.org", "example", digest, output)
            self.assertFalse(output.exists())

    def test_rejects_aggregate_size_limit_before_blob_fetch(self):
        data, digest, payloads = self.fixture()
        manifest = json.loads(data)
        manifest["config"]["size"] = MAX_BLOB
        manifest["layers"][0]["size"] = MAX_BLOB
        data, digest = blob(manifest)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "layout"
            with self.assertRaisesRegex(ValueError, "aggregate size limit"):
                self.run_acquire(data, digest, payloads, output)
            self.assertFalse(output.exists())

    def test_rejects_symlink_destination(self):
        data, digest, payloads = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.mkdir()
            output = Path(directory) / "layout"
            output.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "must not already exist"):
                self.run_acquire(data, digest, payloads, output)
            self.assertEqual(list(target.iterdir()), [])

    def test_rejects_destination_created_during_acquisition(self):
        data, digest, payloads = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "layout"
            def connection(registry, repository, requested):
                output.mkdir(exist_ok=True)
                return Connection(Response(payloads[requested], length=len(payloads[requested])))
            with patch("oci_registry_acquire.fetch_manifest", return_value=(data, MANIFEST_TYPE)), \
                 patch("oci_registry_acquire._connection", side_effect=connection):
                with self.assertRaisesRegex(ValueError, "destination appeared"):
                    acquire("https://example.org", "example", digest, output)
            self.assertTrue(output.is_dir())
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_rejects_existing_destination(self):
        data, digest, payloads = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "must not already exist"):
                self.run_acquire(data, digest, payloads, Path(directory))

    def test_rejects_non_object_manifest_without_creating_layout(self):
        _, _, payloads = self.fixture()
        for malformed in ([], None, "not a manifest", {"schemaVersion": 1, "layers": []}):
            data, digest = blob(malformed)
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "layout"
                with self.assertRaisesRegex(ValueError, "invalid OCI image manifest"):
                    self.run_acquire(data, digest, payloads, output)
                self.assertFalse(output.exists())

    def test_rejects_excessive_descriptor_size_before_network(self):
        data, digest, payloads = self.fixture()
        manifest = json.loads(data)
        manifest["layers"][0]["size"] = MAX_BLOB + 1
        data, digest = blob(manifest)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "layout"
            with self.assertRaisesRegex(ValueError, "invalid blob size"):
                self.run_acquire(data, digest, payloads, output)
            self.assertFalse(output.exists())

if __name__ == "__main__":
    unittest.main()
