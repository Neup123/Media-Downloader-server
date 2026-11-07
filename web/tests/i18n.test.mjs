import test from 'node:test';
import assert from 'node:assert/strict';

import { normalizeLocale, isRtl, translate } from '../src/i18n.ts';

test('browser and saved language codes normalize to supported locales', () => {
  assert.equal(normalizeLocale('el-GR'), 'el');
  assert.equal(normalizeLocale('he-IL'), 'he');
  assert.equal(normalizeLocale('iw_IL'), 'he');
  assert.equal(normalizeLocale('en-US'), 'en');
  assert.equal(normalizeLocale('fr-FR'), 'en');
  assert.equal(normalizeLocale(null), 'en');
});

test('source English is preserved and incomplete translations fall back safely', () => {
  assert.equal(translate('en', 'Download'), 'Download');
  assert.equal(translate('el', 'Download'), 'Λήψη');
  assert.equal(translate('he', 'Download'), 'הורדה');
  assert.equal(translate('he', 'Untranslated API guidance'), 'Untranslated API guidance');
});

test('only Hebrew uses RTL document direction', () => {
  assert.equal(isRtl('he'), true);
  assert.equal(isRtl('el'), false);
  assert.equal(isRtl('en'), false);
});

test('job status and actions translate without changing engine or API data', () => {
  assert.equal(translate('he', 'Delivered ✓ Verified'), 'הועבר ✓ אומת');
  assert.equal(translate('el', 'Try again'), 'Δοκιμάστε ξανά');
  assert.equal(translate('en', 'Queued'), 'Queued');
  assert.equal(translate('el', 'Unknown engine status'), 'Unknown engine status');
});
