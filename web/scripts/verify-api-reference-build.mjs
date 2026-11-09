// The Docker build must contain the styled, compiled API reference, not an unbundled TS page.
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { resolve, dirname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dist = resolve(root, 'dist');
const page = resolve(dist, 'api-reference.html');
assert.ok(existsSync(page), 'Vite must emit api-reference.html');
const html = readFileSync(page, 'utf8');
assert.ok(!html.includes('/src/api-reference.ts'), 'Production page references uncompiled TypeScript');
assert.ok(!/https?:\/\/(?:cdn\.|unpkg\.|jsdelivr\.|fonts\.)/.test(html), 'Remote assets not allowed');
assert.match(html, /id="scalar-reference"/);
const assets = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map(m => m[1]);
assert.ok(assets.some(p => /\.js(?:\?|$)/.test(p)), 'Scalar JavaScript was not bundled');
assert.ok(assets.some(p => /\.css(?:\?|$)/.test(p)), 'Scalar stylesheet was not bundled');
for (const asset of assets) {
  const file = resolve(dist, asset.slice(1).split('?')[0]);
  assert.ok(file.startsWith(dist + sep), 'Asset path escapes dist');
  assert.ok(existsSync(file), 'Missing bundled local asset: ' + asset);
}
console.log('API reference HTML, Scalar JavaScript and stylesheet are locally bundled');
