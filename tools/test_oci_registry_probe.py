"""Offline input and network-boundary regression tests for OCI registry probe."""
import socket
import unittest
from unittest.mock import patch
from oci_registry_probe import probe

D = "sha256:" + "a" * 64

class RegistryProbeTests(unittest.TestCase):
    def test_requires_immutable_digest(self):
        for digest in ("latest", "sha256:" + "A" * 64, "sha512:" + "a" * 128):
            with self.subTest(digest=digest), self.assertRaisesRegex(ValueError, "immutable"):
                probe("https://registry.example.org", "project/artifact", digest)

    def test_refuses_unsafe_repository(self):
        for name in ("project//artifact", "project/../artifact", "/project", "project/", "project/./artifact", "project?x=1"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "repository"):
                probe("https://registry.example.org", name, D)

    def test_requires_bare_https_origin(self):
        for origin in ("http://registry.example.org", "https://user:secret@registry.example.org",
                       "https://registry.example.org:8443", "https://registry.example.org/v2",
                       "https://registry.example.org?x=1", "https://registry.example.org/#fragment", "https://registry.example.org:abc",
                       "https://registry.example.org:999999"):
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "HTTPS origin"):
                probe(origin, "project/artifact", D)

    @patch("oci_registry_probe.socket.getaddrinfo")
    def test_rejects_private_and_mixed_dns(self, dns):
        for addresses in (["127.0.0.1"], ["10.0.0.1"], ["203.0.113.9", "192.168.1.2"]):
            dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses]
            with self.subTest(addresses=addresses), self.assertRaisesRegex(ValueError, "public IP"):
                probe("https://registry.example.org", "project/artifact", D)

if __name__ == "__main__":
    unittest.main()
