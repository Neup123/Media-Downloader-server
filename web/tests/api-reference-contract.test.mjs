import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';

const root = new URL('../', import.meta.url);
const read = path => readFileSync(new URL(path, root), 'utf8');

test('Scalar dependency is pinned and both pages are Vite entry points', () => {
  const manifest = JSON.parse(read('package.json'));
  const lock = JSON.parse(read('package-lock.json'));
  const pinned = manifest.dependencies['@scalar/api-reference'];
  assert.equal(pinned, '1.62.4');
  assert.equal(lock.packages['node_modules/@scalar/api-reference'].version, pinned);
  assert.match(pinned, /^\d+\.\d+\.\d+$/);
  const vite = read('vite.config.ts');
  assert.match(vite, /api-reference\.html/);
  assert.match(vite, /index\.html/);
});

test('Reference imports bundled Scalar CSS and has only local URLs', () => {
  const html = read('api-reference.html');
  const source = read('src/api-reference.ts');
  assert.match(html, /src="\/src\/api-reference\.ts"/);
  assert.match(source, /@scalar\/api-reference\/style\.css/);
  assert.match(source, /url:\s*'\/openapi\.yaml'/);
  assert.match(source, /darkMode:\s*true/);
  assert.doesNotMatch(html + source, /https?:\/\/(?:cdn\.|unpkg\.|jsdelivr\.|fonts\.)/);
});
