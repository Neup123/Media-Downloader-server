import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');

test('input success hint reads Core engine capabilities instead of a hand-coded URL list', () => {
  assert.match(app, /item\.url_schemes/);
  assert.match(app, /advertisedSchemes\.has\(scheme\)/);
  assert.match(app, /selectedEngine === 'auto' \|\| item\.name === selectedEngine/);
  assert.doesNotMatch(app, /\['http:', 'https:', 'ftp:', 'sftp:'\]/);
  assert.doesNotMatch(app, /Enter a public HTTP\(S\), FTP, SFTP/);
  assert.match(app, /!sourceHint\.startsWith\('✓'\)/);
});
