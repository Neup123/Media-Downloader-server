import test from 'node:test';
import assert from 'node:assert/strict';

import { apiErrorMessage } from '../src/api-errors.ts';

test('display Pydantic field paths and human messages, not object serialization', () => {
  const result = apiErrorMessage({detail: [
    {loc: ['body', 'engine'], msg: "Input should be 'yt-dlp'", type: 'literal_error'},
    {loc: ['body', 'overrides', 'format'], msg: 'Invalid format', type: 'value_error'},
  ]}, 422);
  assert.match(result, /body\.engine: Input should be/);
  assert.match(result, /body\.overrides\.format: Invalid format/);
  assert.doesNotMatch(result, /\[object Object\]/);
});

test('batch item errors are helpful without echoing sensitive input URLs', () => {
  const result = apiErrorMessage({
    detail: {
      item: 2, url: 'https://example.org/file?access_token=SECRET_DO_NOT_LOG',
      error: 'Private source is blocked',
    },
  }, 422);
  assert.equal(result, 'Batch item 2: Private source is blocked');
  assert.doesNotMatch(result, /SECRET_DO_NOT_LOG/);
});

test('show business validation messages and use safe HTTP fallback', () => {
  assert.equal(apiErrorMessage({detail: 'Unknown job status'}, 422), 'Unknown job status');
  assert.equal(apiErrorMessage({error: 'n/a'}, 500), 'Request failed (HTTP 500)');
  assert.equal(apiErrorMessage(null, 503), 'Request failed (HTTP 503)');
  assert.equal(apiErrorMessage({detail: []}, 422), 'Request failed (HTTP 422)');
});

test('bound very long and malformed validation error responses', () => {
  const msg = apiErrorMessage({detail: 'x'.repeat(9999)}, 422);
  assert.equal(msg.length, 500);
  const many = apiErrorMessage({detail: Array.from({length: 12}, (_, i) => ({
    loc: ['body', i], msg: 'Error ' + i,
  }))}, 422);
  assert.match(many, /\(\+8 more\)/);
  assert.ok(many.length <= 500);
});
