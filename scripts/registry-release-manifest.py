#!/usr/bin/env python3
"""Generate the release registry manifest for the public, Ephor-free Rebekah OCI archive."""
import hashlib
import json
import os
from pathlib import Path
import re

tag=os.environ.get("GITHUB_REF_NAME","")
repo=os.environ.get("GITHUB_REPOSITORY","")
commit=os.environ.get("GITHUB_SHA","")
if not re.fullmatch(r"v\d+\.\d+\.\d+",tag) or repo.lower()!="tabenius/rebekah" or not re.fullmatch(r"[a-f0-9]{40}",commit):
    raise SystemExit("Requires an immutable semver tag on tabenius/rebekah")
artifact=Path("rebekah-image.tar.gz")
if not artifact.is_file() or artifact.is_symlink() or artifact.stat().st_size<1:
    raise SystemExit("Missing validated Rebekah OCI archive")
digest=hashlib.sha256()
with artifact.open("rb") as input:
    for chunk in iter(lambda:input.read(1024*1024),b""):
        digest.update(chunk)
version=tag[1:]
manifest={"schema":"ragbaz.release/1","repository":repo,"product":"rebekah","version":version,"platform":"linux-amd64","channel":"candidate","sourceCommit":commit,"files":[{"name":artifact.name,"path":f"releases/rebekah/{version}/linux-amd64/{artifact.name}","sha256":digest.hexdigest(),"bytes":artifact.stat().st_size}]}
with open("release.json","x",encoding="utf8") as out:
    json.dump(manifest,out,indent=2)
    out.write("\n")
print("Rebekah immutable OCI release manifest created")
