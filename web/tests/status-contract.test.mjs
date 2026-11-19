import test from 'node:test';
import assert from 'node:assert/strict';
import {JOB_STATUSES, TERMINAL_JOB_STATUSES, isTerminalStatus, isKnownJobStatus} from '../src/job-statuses.generated.ts';

test('standalone job status vocabulary is exact and unknown states fail closed',()=>{
  assert.equal(new Set(JOB_STATUSES).size, JOB_STATUSES.length);
  for(const state of JOB_STATUSES) {
    assert.equal(isKnownJobStatus(state),true);
    assert.equal(isTerminalStatus(state),TERMINAL_JOB_STATUSES.has(state));
  }
  for(const invalid of ['SKIPPED','COMPLETED','done','',null,undefined,0,{}]) {
    assert.equal(isTerminalStatus(invalid),false);
    assert.equal(isKnownJobStatus(invalid),false);
  }
});
