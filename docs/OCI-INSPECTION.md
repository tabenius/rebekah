# Offline OCI inspection interchange (experimental)

Rebekah's `tools/oci_inspect.py` emits a JSON-only, read-only inspection report for a **local OCI image layout**:

```sh
python3 tools/oci_inspect.py /path/to/oci-layout > inspection.json
python3 -m unittest discover -s tools -p 'test_oci*.py' -v
```

The report uses `schemaVersion: ragbaz.oci-inspection/v1` and an `artifacts` array. Each entry identifies a manifest digest, artifact type, optional subject, layer descriptors and five **independent** status fields: `integrity`, `signature`, `identity`, `trust`, `publication`. `subjectStatus` is `none`, `unresolved` or `verified-local-association`. Each artifact also contains a sorted `referrers` array of local verified manifests that declare it as their subject. Subject cycles and duplicate manifest digests are rejected. A verified local association means only that a subject descriptor matches another verified manifest in this layout; it is **not** signature verification, provenance or a factual endorsement.

Consumers (Pieces, Mouseion, WeftMark) MUST treat this JSON as untrusted input unless produced by a trusted local invocation against the exact selected layout. They MUST NOT infer permission to publish, sign, execute, import as an editable manuscript, or disclose any payload. The report is metadata only; it never embeds layer bytes or tokens.

This is an early interoperability boundary, **not** a complete OCI 1.1 conformance validator. It does not resolve remote registries, verify signatures, fetch remote referrers or establish a complete registry-wide referrer closure, validate attestations or enforce host application policy. Unknown media types are not executed. Imports should remain explicit, consent-gated and separate from Mouseion's Nostoi publication chain.
