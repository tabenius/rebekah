# Cross-repository OCI consumer smoke test

The producer is deliberately **not** the Python handoff publisher: `tools/oci_fixture.mjs`
constructs OCI manifest and blob descriptors using Node.js and SHA-256. The
Python inspector verifies the layout and emits `ragbaz.oci-inspection/v1` JSON.

Run the self-contained interoperability suite:

```sh
python3 -m unittest discover -s tools -p 'test_oci*.py' -v
```

Run the full read-only consumer test with existing local checkouts:

```sh
node tools/oci_consumer_e2e.mjs ../pieces.ragbaz ../Mouseion
```

The command exercises the independent producer, inspector, Pieces preview
adapter, and Mouseion museum adapter. It must not require registry credentials,
upload any artifact, or mutate Pieces/Mouseion state.

Both consumer repositories are private. The Rebekah CI workflow intentionally
**does not** attempt cross-repository checkout with an unconfigured token.
To automate this full test, provide a narrowly scoped read-only GitHub App
installation token or equivalent in a dedicated protected workflow. Do not
embed credentials in the fixture, source, logs, or OCI layers.

A successful run establishes format compatibility and digest integrity for
this fixture. It does **not** authenticate authors, verify signatures,
establish consent, or grant publication/exhibition permissions.
