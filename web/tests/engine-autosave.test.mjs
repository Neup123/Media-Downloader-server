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
