import test from 'node:test';
import assert from 'node:assert/strict';
import {EngineAutosaveQueue} from '../src/engine-autosave.ts';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

test('hydration does not write; rapid edits debounce to newest settings', async () => {
  const writes = [], states = [];
  const queue = new EngineAutosaveQueue(async payload => { writes.push(payload); },
    state => states.push(state), 10);
  queue.hydrate({format:'old'});
  await sleep(20);
  assert.deepEqual(writes, []);
  queue.change({format:'one'});
  queue.change({format:'two'});
  await sleep(40);
  assert.deepEqual(writes, [{format:'two'}]);
  assert.equal(states.at(-1), 'saved');
  queue.change({format:'two'});
  await sleep(25);
  assert.equal(writes.length, 1);
  queue.dispose();
});

test('in-flight requests serialize and the latest edit always wins', async () => {
  const writes = [];
  let release;
  let running = false;
  const queue = new EngineAutosaveQueue(async value => {
    assert.equal(running, false, 'concurrent engine PUT requests must not race');
    running = true;
    writes.push(value.format);
    if(value.format === 'one') {
      await new Promise(resolve => {release = resolve;});
    }
    running = false;
  }, () => {}, 6);
  queue.hydrate({format:'old'});
  queue.change({format:'one'});
  await sleep(15);
  assert.equal(writes[0], 'one');
  queue.change({format:'two'});
  await sleep(15);
  assert.deepEqual(writes, ['one']);
  release();
  await sleep(35);
  assert.deepEqual(writes, ['one','two']);
  queue.dispose();
});

test('failed autosave remains visible and explicit retry succeeds', async () => {
  let fail = true;
  const writes = [], statuses = [];
  const queue = new EngineAutosaveQueue(async value => {
    writes.push(value);
    if (fail) throw Error('temporary network issue');
  }, state => statuses.push(state), 5);
  queue.hydrate({download:true});
  queue.change({download:false});
  await sleep(30);
  assert.equal(statuses.at(-1), 'error');
  fail = false;
  queue.retry();
  await sleep(30);
  assert.equal(statuses.at(-1), 'saved');
  assert.equal(writes.length, 2);
  queue.dispose();
});

test('disposed queue never persists or changes status', async () => {
  const writes = [], statuses = [];
  const queue = new EngineAutosaveQueue(async v => writes.push(v), s => statuses.push(s), 10);
  queue.hydrate({value:1});
  queue.change({value:2});
  queue.dispose();
  await sleep(30);
  assert.deepEqual(writes, []);
  assert.equal(statuses.at(-1), 'pending');
});

test('invalid advanced data pauses pending changes until repaired', async () => {
  const writes = [], states = [];
  const queue = new EngineAutosaveQueue(async value => writes.push(value),
    state => states.push(state), 10);
  queue.hydrate({options:{old:'yes'}});
  queue.change({options:{changed:'yes'}});
  queue.pause();
  await sleep(30);
  assert.deepEqual(writes, []);
  assert.equal(states.at(-1), 'error');
  queue.change({options:{repaired:'yes'}});
  await sleep(35);
  assert.deepEqual(writes, [{options:{repaired:'yes'}}]);
  assert.equal(states.at(-1), 'saved');
  queue.dispose();
});


test('pausing invalid data during an in-flight save never reports false success', async () => {
  const writes = [], states = [];
  let releaseFirst;
  const queue = new EngineAutosaveQueue(async draft => {
    writes.push(draft);
    if (draft.name === 'Old valid edit') {
      await new Promise(resolve => { releaseFirst = resolve; });
    }
  }, state => states.push(state), 6);

  queue.hydrate({name: 'Original'});
  queue.change({name: 'Old valid edit'});
  await sleep(18);
  assert.deepEqual(writes, [{name: 'Old valid edit'}]);

  queue.pause();
  assert.equal(states.at(-1), 'error');
  releaseFirst();
  await sleep(30);
  assert.equal(states.at(-1), 'error',
    'A prior valid HTTP response must not make invalid current form data appear saved');

  queue.change({name: 'Corrected edit'});
  await sleep(38);
  assert.deepEqual(writes, [{name: 'Old valid edit'}, {name: 'Corrected edit'}]);
  assert.equal(states.at(-1), 'saved');
  queue.dispose();
});

test('switching away from a pending preset prevents later unsent writes', async () => {
  const writes = [], states = [];
  const queue = new EngineAutosaveQueue(async value => writes.push(value),
    state => states.push(state), 25);
  queue.hydrate({name: 'One'});
  queue.change({name: 'Two'});
  queue.dispose();
  await sleep(50);
  assert.deepEqual(writes, []);
  assert.equal(states.at(-1), 'pending');
});


test('hydration and form drafts with reordered keys are semantically identical', async () => {
  const writes = [], states = [];
  const queue = new EngineAutosaveQueue(async draft => writes.push(draft),
    state => states.push(state), 6);
  queue.hydrate({
    name: 'Chromium site preset', engine: 'yt-dlp',
    description: 'From API', settings: {format: 'best', nested: {alpha: 1, beta: 2}},
    is_shared: false,
  });
  queue.change({
    settings: {nested: {beta: 2, alpha: 1}, format: 'best'},
    is_shared: false, description: 'From API', engine: 'yt-dlp',
    name: 'Chromium site preset',
  });
  await sleep(25);
  assert.deepEqual(writes, [], 'Opening an existing preset must not update the server');
  assert.equal(states.at(-1), 'saved');

  queue.change({
    settings: {format: 'best', nested: {alpha: 1, beta: 2}},
    is_shared: true, description: 'From API', engine: 'yt-dlp',
    name: 'Chromium site preset',
  });
  await sleep(28);
  assert.equal(writes.length, 1, 'An actual shared-state change must persist');
  assert.equal(writes[0].is_shared, true);
  queue.dispose();
});

test('array order remains meaningful even with canonical object keys', async () => {
  const writes = [];
  const queue = new EngineAutosaveQueue(async draft => writes.push(draft), () => {}, 5);
  queue.hydrate({items: ['first', 'second']});
  queue.change({items: ['second', 'first']});
  await sleep(28);
  assert.deepEqual(writes, [{items: ['second', 'first']}]);
  queue.dispose();
});
