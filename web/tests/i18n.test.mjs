import test from 'node:test';
import assert from 'node:assert/strict';

import { LOCALES, normalizeLocale, isRtl, translate } from '../src/i18n.ts';

test('English base and explicitly supported Hebrew locale, including legacy identifier', () => {
  assert.deepEqual(LOCALES, ['en', 'he']);
  assert.equal(normalizeLocale('he-IL'), 'he');
  assert.equal(normalizeLocale('iw_IL'), 'he');
  assert.equal(normalizeLocale('en-US'), 'en');
  assert.equal(normalizeLocale('fr-FR'), 'en');
  assert.equal(normalizeLocale(null), 'en');
});

test('English source fallback and full Hebrew job-card translation', () => {
  assert.equal(translate('en', 'Download'), 'Download');
  assert.equal(translate('he', 'Download'), 'הורדה');
  assert.equal(translate('he', 'Library'), 'ספריית ההורדות');
  assert.equal(translate('he', 'Delivered ✓ Verified'), 'הועבר ✓ אומת');
  assert.equal(translate('he', 'Try again'), 'ניסיון חוזר');
  assert.equal(translate('he', 'Technical log (advanced)'), 'יומן טכני (מתקדם)');
  assert.equal(translate('he', 'Untranslated API guidance'), 'Untranslated API guidance');
  assert.equal(translate('en', 'Queued'), 'Queued');
});

test('only Hebrew uses RTL and switching back restores English LTR', () => {
  assert.equal(isRtl('he'), true);
  assert.equal(isRtl('en'), false);
  assert.equal(isRtl(normalizeLocale('fr-FR')), false);
});
