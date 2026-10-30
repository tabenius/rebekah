#!/usr/bin/env node
// Execute the two consumer adapters against the actual Python inspector report.
// Requires local checkouts supplied explicitly; no network or publication.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

const [piecesPath, mouseionPath] = process.argv.slice(2);
if (!piecesPath || !mouseionPath) {
  throw new Error('Usage: node tools/oci_consumer_e2e.mjs <pieces-checkout> <mouseion-checkout>');
}
const root = mkdtempSync(join(tmpdir(), 'ragbaz-oci-e2e-'));
try {
  const producerOutput = JSON.parse(execFileSync('node', [new URL('./oci_fixture.mjs', import.meta.url).pathname, root], {encoding: 'utf8'}));
  const report = JSON.parse(execFileSync('python3', [new URL('./oci_inspect.py', import.meta.url).pathname, root], {encoding: 'utf8'}));
  const pieces = await import(pathToFileURL(resolve(piecesPath, 'shared/oci-inspection.js')).href);
  const mouseion = await import(pathToFileURL(resolve(mouseionPath, 'src/oci-inspection.mjs')).href);
  const preview = pieces.previewOciImport(report);
  const exhibits = mouseion.summarizeOciInspection(report);
  assert.equal(preview.artifacts.length, 2);
  assert.equal(exhibits.length, 2);
  assert.equal(preview.actions.importContent, false);
  assert.equal(preview.actions.publish, false);
  assert.equal(exhibits.find(x => x.manifestDigest === producerOutput.base).referrerDigests[0], producerOutput.referrer);
  assert.ok(exhibits.every(x => x.exhibition === 'not-authorized'));
  console.log('OCI producer -> inspector -> Pieces -> Mouseion: passed (read-only)');
} finally {
  rmSync(root, {recursive: true, force: true});
}
