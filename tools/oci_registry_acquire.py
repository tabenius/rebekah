#!/usr/bin/env python3
"""Fetch one immutable OCI image manifest into a bounded, read-only OCI layout.

Anonymous HTTPS only. No redirects, credentials, nested indexes, or publication.
"""
import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from oci_registry_probe import _connection, fetch_manifest, DIGEST, MAX_MANIFEST

MAX_BLOB = 128 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_LAYERS = 256
MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"

def _descriptor(value):
    if not isinstance(value, dict) or not isinstance(value.get("digest"), str) or not DIGEST.fullmatch(value["digest"]):
        raise ValueError("invalid blob digest")
    if type(value.get("size")) is not int or not 0 <= value["size"] <= MAX_BLOB:
        raise ValueError("invalid blob size")
    if not isinstance(value.get("mediaType"), str) or not value["mediaType"]:
        raise ValueError("invalid blob media type")
    return value

def acquire(registry, repository, digest, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("destination must not already exist")
    data, media_type = fetch_manifest(registry, repository, digest)
    if media_type != MANIFEST_TYPE:
        raise ValueError("only single OCI image manifests supported; indexes require explicit selection")
    manifest = json.loads(data)
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2:
        raise ValueError("invalid OCI image manifest")
    if manifest.get("mediaType", MANIFEST_TYPE) != MANIFEST_TYPE or not isinstance(manifest.get("layers"), list) or len(manifest["layers"]) > MAX_LAYERS:
        raise ValueError("invalid OCI image manifest")
    descriptors = [_descriptor(manifest.get("config"))] + [_descriptor(x) for x in manifest["layers"]]
    unique = {d["digest"]: d for d in descriptors}
    for d in descriptors:
        if unique[d["digest"]]["size"] != d["size"] or unique[d["digest"]]["mediaType"] != d["mediaType"]:
            raise ValueError("conflicting descriptors for same digest")
    total = len(data) + sum(d["size"] for d in unique.values())
    if total > MAX_TOTAL:
        raise ValueError("OCI layout exceeds aggregate size limit")
    # Fetch and verify everything before creating output. Failed acquisitions leave no layout.
    blobs = {digest: data}
    for item in unique.values():
        conn = _connection(registry, repository, item["digest"])
        try:
            conn.request("GET", f"/v2/{repository}/blobs/{item['digest']}", headers={"User-Agent": "ragbaz-oci-readonly-acquire/1"})
            response = conn.getresponse()
            if response.status != 200:
                raise ValueError(f"registry blob request returned HTTP {response.status}; redirects are not followed")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) != item["size"]):
                raise ValueError("OCI blob Content-Length mismatch")
            payload = response.read(item["size"] + 1)
            if len(payload) != item["size"] or hashlib.sha256(payload).hexdigest() != item["digest"][7:]:
                raise ValueError("OCI blob size or SHA-256 mismatch")
            blobs[item["digest"]] = payload
        finally:
            conn.close()
    # Stage on the same filesystem and publish only after every file is written.
    # A failed write must not expose a partial OCI layout at the requested path.
    stage = Path(tempfile.mkdtemp(prefix=".oci-acquire-", dir=destination.parent))
    try:
        (stage / "blobs" / "sha256").mkdir(parents=True)
        for key, payload in blobs.items():
            (stage / "blobs" / "sha256" / key[7:]).write_bytes(payload)
        (stage / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')
        (stage / "index.json").write_text(json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [{"digest": digest, "size": len(data), "mediaType": MANIFEST_TYPE}]}) + "\n")
        stage.rename(destination)
    except BaseException:
        shutil.rmtree(stage)
        raise
    return {"layout": str(destination), "manifestDigest": digest, "verifiedBlobs": len(blobs),
            "verifiedBytes": total, "signature": "not-verified", "publication": "not-authorized"}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(acquire(args.registry, args.repository, args.digest, args.output), indent=2))
