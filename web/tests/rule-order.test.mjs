import test from 'node:test';
import assert from 'node:assert/strict';
import {moveRule,stepRule,isReordered} from '../src/rule-order.ts';

const rows = [{id:'a',enabled:true},{id:'b',enabled:false},{id:'c',enabled:true}];
const ids = rules => rules.map(row => row.id);

test('drag before/after preserves identities, disabled entries and original snapshot', () => {
  assert.deepEqual(ids(moveRule(rows,'c','a','before')),['c','a','b']);
  assert.deepEqual(ids(moveRule(rows,'a','c','after')),['b','c','a']);
  assert.equal(moveRule(rows,'c','a')[0].id,'c');
  assert.equal(moveRule(rows,'c','a')[2].enabled,false);
  assert.deepEqual(ids(rows),['a','b','c']);
});
test('keyboard order supports first, last and out-of-bounds cases', () => {
  assert.deepEqual(ids(stepRule(rows,'b',-1)),['b','a','c']);
  assert.deepEqual(ids(stepRule(rows,'a',1)),['b','a','c']);
  assert.deepEqual(ids(stepRule(rows,'c',1)),['a','b','c']);
  assert.deepEqual(ids(stepRule(rows,'a',-1)),['a','b','c']);
});
test('unknown ids and no-op drags never change the ordering', () => {
  assert.deepEqual(ids(moveRule(rows,'missing','a')),ids(rows));
  assert.deepEqual(ids(moveRule(rows,'a','missing')),ids(rows));
  assert.deepEqual(ids(moveRule(rows,'a','a')),ids(rows));
  assert.equal(isReordered(rows,moveRule(rows,'a','a')),false);
  assert.equal(isReordered(rows,moveRule(rows,'b','a')),true);
});
