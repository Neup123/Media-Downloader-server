import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {setOptionOrDefault} from '../src/engine-option-default.ts';

const ui = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');

test('both select updaters use the same omission path for Engine default', () => {
  assert.match(ui, /function updateGetSauceOption[\s\S]*?setOptionOrDefault\(previous, key, value,/);
  assert.match(ui, /function updateSvtOption[\s\S]*?setOptionOrDefault\(previous, key, value,/);
});

test('global and override choices round-trip to an omitted key, not an empty string', () => {
  for (const [key, nondefault] of [['merge', 'cbz'], ['output_format', 'mkv']]) {
    for (const scope of ['global', 'override']) {
      const chosen = setOptionOrDefault({workers:4, [key]:nondefault}, key, '', '');
      assert.equal(Object.hasOwn(chosen, key), false, scope + '/' + key);
      assert.deepEqual(JSON.parse(JSON.stringify(chosen)), {workers:4});
      assert.equal(Object.hasOwn(setOptionOrDefault(chosen, key, nondefault, nondefault), key), true);
    }
  }
});
