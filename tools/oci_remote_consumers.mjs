#!/usr/bin/env node
// Consume an independently verified remote OCI report without granting actions.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const [reportPath, piecesPath, mouseionPath, expectedDigest] = process.argv.slice(2);
if (!reportPath || !piecesPath || !mouseionPath || !expectedDigest) {
  throw new Error('Usage: node tools/oci_remote_consumers.mjs <report.json> <pieces> <mouseion> <sha256:digest>');
}
const report = JSON.parse(readFileSync(reportPath, 'utf8'));
const pieces = await import(pathToFileURL(resolve(piecesPath, 'shared/oci-inspection.js')).href);
const mouseion = await import(pathToFileURL(resolve(mouseionPath, 'src/oci-inspection.mjs')).href);
const preview = pieces.previewOciImport(report);
const exhibits = mouseion.summarizeOciInspection(report);
assert.equal(preview.artifacts.length, 1, 'single remote OCI manifest expected');
assert.equal(exhibits.length, 1, 'single remote OCI manifest expected');
assert.equal(preview.artifacts[0].digest, expectedDigest);
assert.equal(exhibits[0].manifestDigest, expectedDigest);
for (const [action, enabled] of Object.entries(preview.actions)) {
  assert.equal(enabled, false, 'remote OCI preview must not authorize ' + action);
}
assert.equal(exhibits[0].exhibition, 'not-authorized');
console.log('Remote registry -> verified layout -> Rebekah -> Pieces + Mouseion: passed (read-only)');
