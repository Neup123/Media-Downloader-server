import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const ui=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8');

test('finite option choices render native labeled selects, not free text controls',()=>{
  assert.match(ui,/field\.type === 'enum' && field\.choices\?\.length/);
  assert.match(ui,/<select aria-label=\{field\.label\} value=\{values\[field\.key\]/);
  assert.match(ui,/field\.choices\.map\(choice => <option key=\{choice\} value=\{choice\}>/);
  assert.match(ui,/<option value="">Engine default<\/option>/);
  assert.match(ui,/choices\?: string\[\] \| null/);
});
