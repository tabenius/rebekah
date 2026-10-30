#!/usr/bin/env python3
"""Read-only external OCI Distribution manifest probe.

No credentials, redirects, or blob downloads. A registry manifest is untrusted
until a separate inspector verifies the complete OCI layout.
"""
import argparse
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
from urllib.parse import urlsplit

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
MANIFEST_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
)
MAX_MANIFEST = 4 * 1024 * 1024

def _connection(registry, repository, digest):
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]{0,254}", repository) or any(segment in ("", ".", "..") for segment in repository.split("/")):
        raise ValueError("invalid repository")
    if not DIGEST.fullmatch(digest):
        raise ValueError("require immutable sha256 digest")
    parsed = urlsplit(registry)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("registry must be a bare HTTPS origin")
    host = parsed.hostname
    if not re.fullmatch(r"[a-z0-9.-]+", host, re.ASCII) or host.startswith("-") or host.endswith("."):
        raise ValueError("registry host must be a plain ASCII DNS name")
    # Resolve and pin the selected public address to avoid DNS rebinding.
    candidates = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    public = []
    for family, _, _, _, address in candidates:
        ip = ipaddress.ip_address(address[0])
        if ip.is_global:
            public.append((family, str(ip)))
    if not public or len(public) != len(candidates):
        raise ValueError("registry DNS must resolve exclusively to public IP addresses")
    family, address = public[0]
    class PinnedConnection(http.client.HTTPSConnection):
        def connect(self):
            self.sock = socket.socket(family, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect((address, 443))
            self.sock = self._context.wrap_socket(self.sock, server_hostname=host)
    return PinnedConnection(host, timeout=15, context=ssl.create_default_context())

def fetch_manifest(registry, repository, digest):
    conn = _connection(registry, repository, digest)
    try:
        path = f"/v2/{repository}/manifests/{digest}"
        conn.request("GET", path, headers={"Accept": ", ".join(MANIFEST_TYPES), "User-Agent": "ragbaz-oci-readonly-probe/1"})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError(f"registry returned HTTP {response.status}; anonymous manifest access required")
        media_type = response.getheader("Content-Type", "").split(";")[0].strip()
        if media_type not in MANIFEST_TYPES:
            raise ValueError("unsupported manifest media type")
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > MAX_MANIFEST):
            raise ValueError("manifest too large")
        data = response.read(MAX_MANIFEST + 1)
        if len(data) > MAX_MANIFEST or hashlib.sha256(data).hexdigest() != digest[7:]:
            raise ValueError("manifest size or SHA-256 mismatch")
        manifest = json.loads(data)
        if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2:
            raise ValueError("invalid OCI manifest JSON")
        return data, media_type
    finally:
        conn.close()

def probe(registry, repository, digest):
    data, media_type = fetch_manifest(registry, repository, digest)
    return {"registry": registry, "repository": repository, "digest": digest, "mediaType": media_type,
            "size": len(data), "integrity": "manifest-sha256-verified",
            "blobs": "not-fetched", "signature": "not-verified", "identity": "not-verified",
            "publication": "not-authorized"}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, help="bare HTTPS registry origin")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--digest", required=True, help="immutable sha256 digest")
    args = parser.parse_args()
    print(json.dumps(probe(args.registry, args.repository, args.digest), indent=2))
