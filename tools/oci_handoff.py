#!/usr/bin/env python3
"""Create a minimal OCI 1.1 image layout for a workspace handoff (offline, stdlib)."""
import argparse
import hashlib
import json
from pathlib import Path

OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_EMPTY = "application/vnd.oci.empty.v1+json"
HANDOFF = "application/vnd.ragbaz.workspace-handoff.v1+json"

def encoded(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

def blob(root, data):
    digest = hashlib.sha256(data).hexdigest()
    path = root / "blobs" / "sha256" / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != data:
        raise ValueError("digest collision or corrupt existing blob")
    path.write_bytes(data)
    return {"mediaType": "application/octet-stream", "digest": "sha256:" + digest, "size": len(data)}

def validate(record):
    if record.get("schemaVersion") != "0.1":
        raise ValueError("unsupported schemaVersion")
    for field in ("taskId", "sessionId"):
        if not isinstance(record.get(field), str) or not record[field]:
            raise ValueError("missing " + field)
    repo = record.get("repository", {})
    commit = repo.get("commit", "")
    if not isinstance(commit, str) or len(commit) not in (40, 64) or any(c not in "0123456789abcdef" for c in commit):
        raise ValueError("invalid repository commit")
    artifact = record.get("artifact", {})
    digest = artifact.get("ociDigest", "")
    if not valid_digest(digest) or not artifact.get("mediaType"):
        raise ValueError("invalid artifact descriptor")
    evidence = record.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list")
    for entry in evidence:
        if entry.get("kind") not in ("observation", "assertion", "test", "attestation") or not valid_digest(entry.get("digest", "")) or not entry.get("source"):
            raise ValueError("invalid evidence entry")

def valid_digest(value):
    return isinstance(value, str) and value.startswith("sha256:") and len(value) == 71 and all(c in "0123456789abcdef" for c in value[7:])

def publish(record, root):
    validate(record)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    payload = blob(root, encoded(record))
    payload["mediaType"] = HANDOFF
    config = blob(root, b"{}")
    config["mediaType"] = OCI_EMPTY
    manifest = {"schemaVersion": 2, "mediaType": OCI_MANIFEST, "artifactType": HANDOFF, "config": config, "layers": [payload]}
    manifest_bytes = encoded(manifest)
    descriptor = blob(root, manifest_bytes)
    descriptor["mediaType"] = OCI_MANIFEST
    descriptor["artifactType"] = HANDOFF
    (root / "oci-layout").write_bytes(encoded({"imageLayoutVersion": "1.0.0"}))
    (root / "index.json").write_bytes(encoded({"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [descriptor]}))
    return descriptor["digest"]

def verify(root):
    root = Path(root)
    index = json.loads((root / "index.json").read_bytes())
    if index.get("mediaType") != OCI_INDEX or len(index.get("manifests", [])) != 1:
        raise ValueError("invalid index")
    def read(desc):
        digest = desc["digest"]
        if not valid_digest(digest):
            raise ValueError("invalid digest")
        data = (root / "blobs" / "sha256" / digest[7:]).read_bytes()
        if len(data) != desc["size"] or hashlib.sha256(data).hexdigest() != digest[7:]:
            raise ValueError("digest or size mismatch")
        return data
    manifest = json.loads(read(index["manifests"][0]))
    if manifest.get("artifactType") != HANDOFF or len(manifest.get("layers", [])) != 1:
        raise ValueError("unexpected artifact")
    read(manifest["config"])
    record = json.loads(read(manifest["layers"][0]))
    validate(record)
    return record

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["publish", "verify"])
    p.add_argument("layout")
    p.add_argument("--record")
    args = p.parse_args()
    if args.command == "publish":
        if not args.record:
            p.error("publish requires --record")
        print(publish(json.loads(Path(args.record).read_text()), args.layout))
    else:
        print(json.dumps(verify(args.layout), indent=2))
