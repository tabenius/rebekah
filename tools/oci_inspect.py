#!/usr/bin/env python3
"""Bounded, read-only OCI image-layout inspector. Integrity is not trust."""
import hashlib
import json
import re
from pathlib import Path

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_INDEX = 1024 * 1024
MAX_MANIFEST = 4 * 1024 * 1024
MAX_BLOB = 128 * 1024 * 1024
MAX_DESCRIPTORS = 256

def _object(data, label):
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid {label} JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid {label} object")
    return value

def _read(root, descriptor, limit):
    if not isinstance(descriptor, dict):
        raise ValueError("invalid OCI descriptor")
    digest, size = descriptor.get("digest"), descriptor.get("size")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ValueError("unsupported OCI digest")
    if type(size) is not int or not 0 <= size <= limit:
        raise ValueError("invalid OCI blob size")
    path = root / "blobs" / "sha256" / digest[7:]
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing or unsafe OCI blob")
    if path.stat().st_size != size:
        raise ValueError("OCI blob size mismatch")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest[7:]:
        raise ValueError("OCI blob digest mismatch")
    return data

def inspect(root):
    root = Path(root)
    layout_path = root / "oci-layout"
    index_path = root / "index.json"
    if layout_path.is_symlink() or index_path.is_symlink():
        raise ValueError("unsafe OCI layout path")
    if layout_path.stat().st_size > 4096 or index_path.stat().st_size > MAX_INDEX:
        raise ValueError("OCI metadata exceeds limit")
    if _object(layout_path.read_bytes(), "layout").get("imageLayoutVersion") != "1.0.0":
        raise ValueError("unsupported OCI layout")
    index = _object(index_path.read_bytes(), "index")
    manifests = index.get("manifests")
    if index.get("schemaVersion") != 2 or not isinstance(manifests, list) or len(manifests) > MAX_DESCRIPTORS:
        raise ValueError("invalid OCI index")
    artifacts = []
    for descriptor in manifests:
        manifest = _object(_read(root, descriptor, MAX_MANIFEST), "manifest")
        if manifest.get("schemaVersion") != 2 or not isinstance(manifest.get("layers"), list) or len(manifest["layers"]) > MAX_DESCRIPTORS:
            raise ValueError("invalid OCI manifest")
        _read(root, manifest.get("config"), MAX_BLOB)
        for layer in manifest["layers"]:
            _read(root, layer, MAX_BLOB)
        artifacts.append({
            "digest": descriptor["digest"],
            "artifactType": manifest.get("artifactType"),
            "subject": manifest.get("subject"),
            "layers": [{"digest": x["digest"], "mediaType": x.get("mediaType"), "size": x["size"]} for x in manifest["layers"]],
            "integrity": "verified",
            "signature": "not-verified",
            "identity": "not-verified",
            "trust": "not-assessed",
            "publication": "not-authorized",
        })
    return {"schemaVersion": "ragbaz.oci-inspection/v1", "artifacts": artifacts}
