#!/usr/bin/env node
// Independent OCI layout fixture producer. No Rebekah Python imports.
import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const root = process.argv[2];
if (!root) throw new Error('Usage: node tools/oci_fixture.mjs <output-directory>');
const manifestType = 'application/vnd.oci.image.manifest.v1+json';
const emptyType = 'application/vnd.oci.empty.v1+json';
const artifactType = 'application/vnd.ragbaz.interop-fixture.v1+json';
const encode = (obj) => Buffer.from(JSON.stringify(obj));
async function blob(data, mediaType) {
  const bytes = Buffer.isBuffer(data) ? data : encode(data);
  const hash = createHash('sha256').update(bytes).digest('hex');
  await mkdir(join(root, 'blobs', 'sha256'), { recursive: true });
  await writeFile(join(root, 'blobs', 'sha256', hash), bytes);
  return { mediaType, digest: 'sha256:' + hash, size: bytes.length };
}
const config = await blob({}, emptyType);
const payload = await blob({ fixture: 'independent-node-producer', revision: 1 }, artifactType);
const base = await blob({
  schemaVersion: 2, mediaType: manifestType, artifactType,
  config, layers: [payload]
}, manifestType);
const referrer = await blob({
  schemaVersion: 2, mediaType: manifestType,
  artifactType: 'application/vnd.ragbaz.interop-reference.v1+json',
  config, layers: [], subject: base
}, manifestType);
await writeFile(join(root, 'oci-layout'), encode({ imageLayoutVersion: '1.0.0' }));
await writeFile(join(root, 'index.json'), encode({
  schemaVersion: 2, mediaType: 'application/vnd.oci.image.index.v1+json',
  manifests: [base, referrer]
}));
console.log(JSON.stringify({ base: base.digest, referrer: referrer.digest }));
