import test from 'node:test';
import assert from 'node:assert/strict';
import {VIDEO_QUALITY, qualityFromFormat, formatFromQuality, validPresetSettings} from '../src/ui-options.ts';

test('human quality choices produce valid yt-dlp format expressions', () => {
  assert.equal(VIDEO_QUALITY.length, 4);
  for (const {id, format} of VIDEO_QUALITY) {
    assert.equal(formatFromQuality(id), format);
    assert.equal(qualityFromFormat(format), id);
  }
  assert.equal(formatFromQuality('unknown'), null);
  assert.equal(qualityFromFormat('best[height<=360]/best'), 'custom');
});
test('advanced preset JSON must remain an object', () => {
  assert.equal(validPresetSettings(null), false);
  assert.equal(validPresetSettings([]), false);
  assert.equal(validPresetSettings({format:'best'}), true);
});
