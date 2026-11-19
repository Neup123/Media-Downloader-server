import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const ui = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');

test('administrator key defaults to session storage and discards legacy persistent keys', () => {
  assert.match(ui, /const \[apiKey, setApiKey\] = useState\(savedAdminKey\)/);
  assert.match(ui, /const \[connectionDraft, setConnectionDraft\] = useState\(savedAdminKey\)/);
  assert.match(ui, /localStorage\.removeItem\(ADMIN_KEY_STORAGE\);\s*return sessionStorage\.getItem\(ADMIN_KEY_STORAGE\)/);
  assert.match(ui, /storeAdminKey\(key, rememberBrowser\)/);
  assert.doesNotMatch(ui, /localStorage\.setItem\('downloader-api-key', apiKey\)/);
});

test('persistent key retention is explicit and disconnect clears both storage scopes', () => {
  assert.match(ui, /localStorage\.getItem\(REMEMBER_KEY_STORAGE\) === 'yes'/);
  assert.match(ui, /Remember this browser after the session ends/);
  assert.match(ui, /if \(apiKey\) storeAdminKey\(apiKey, remember\)/);
  assert.match(ui, /storeAdminKey\('', false\);/);
  assert.match(ui, /sessionStorage\.removeItem\(ADMIN_KEY_STORAGE\)/);
});
