import test from 'node:test';
import assert from 'node:assert/strict';
import {previewSelectionSummary} from '../src/preview-selection-size.ts';

const entries=[
  {index:1,size_bytes:1048576,size_estimate_bytes:3000000},
  {index:2,size_bytes:null,size_estimate_bytes:2097152},
  {index:3,size_bytes:null,size_estimate_bytes:null},
];

test('selection reports precise, estimated, partially known, and zero counts',()=>{
  assert.equal(previewSelectionSummary(entries,[]),'0 of 3 items selected · 0 B selected');
  assert.equal(previewSelectionSummary(entries,[1]),'1 of 3 items selected · 1.0 MiB for 1 sized item');
  assert.equal(previewSelectionSummary(entries,[2]),'1 of 3 items selected · About 2.0 MiB for 1 sized item');
  assert.equal(previewSelectionSummary(entries,[3]),'1 of 3 items selected · size unknown for 1 item');
  assert.equal(previewSelectionSummary(entries,[1,2,3]),'3 of 3 items selected · About 3.0 MiB for 2 sized items · size unknown for 1 item');
});

test('invalid or oversized values do not create fictional selected totals',()=>{
  assert.match(previewSelectionSummary([
    {index:1,size_bytes:-1},{index:2,size_bytes:Number.POSITIVE_INFINITY},
    {index:3,size_bytes:Number.MAX_SAFE_INTEGER},
    {index:4,size_bytes:100},
  ],[1,2,3,4]),/size unknown for 3 items/);
});
