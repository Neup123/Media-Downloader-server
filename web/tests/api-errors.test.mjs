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
  assert.equal(apiErrorMessage({error: 'n/a'}, 500), 'The downloader service could not complete this request. Try again or check its connection.');
  assert.equal(apiErrorMessage(null, 503), 'The downloader service could not complete this request. Try again or check its connection.');
  assert.equal(apiErrorMessage({detail: []}, 422), 'Some details were not accepted. Check the fields and try again.');
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

test('Files and destination conflicts explain how to recover without raw codes', () => {
  const disconnected = apiErrorMessage({detail: 'Nextcloud Files not connected: app password missing'}, 409);
  assert.match(disconnected, /Connect them once in My settings/);
  const folder = apiErrorMessage({detail: 'Destination folder unavailable'}, 409);
  assert.match(folder, /Check the folder still exists/);
  assert.doesNotMatch(disconnected + folder, /HTTP 409/);
});

test('authentication and upstream failures have clear next steps', () => {
  assert.match(apiErrorMessage({detail: 'Invalid API key'}, 401), /Reconnect in My settings/);
  assert.match(apiErrorMessage({detail: 'Access denied'}, 403), /Ask your administrator/);
  assert.match(apiErrorMessage({detail: 'Traceback: INTERNAL_SECRET'}, 500), /Try again/);
  assert.doesNotMatch(apiErrorMessage({detail: 'Traceback: INTERNAL_SECRET'}, 500), /INTERNAL_SECRET|HTTP/);
  assert.match(apiErrorMessage(null, 404), /Refresh the list/);
  assert.match(apiErrorMessage(null, 429), /Try again shortly/);
});
