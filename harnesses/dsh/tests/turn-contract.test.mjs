import assert from 'node:assert/strict';
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { readTurnInput, readTurnRecord, sha256, turnOutputPath, validateTurnInput, validateTurnOutcome, validateTurnRecord, writeTurnRecord } from '../scripts/lib/turn-contract.mjs';

const input = { version: 1, taskId: 'task-1', attemptId: 'attempt-1', generation: 1, turnId: 'turn-1', resumeMode: 'initial', previousSessionId: null, context: {}, executionWorkspace: {} };
const outcome = { disposition: 'completed', summary: 'Implemented and checked.', remaining: [], decisions: [], artifacts: ['result.txt'], request: null };
const expected = { input, inputSha256: sha256(JSON.stringify(input)), promptSha256: sha256('task') };
const record = { ...input, sessionId: 'root-1', inputSha256: expected.inputSha256, promptSha256: expected.promptSha256, outcome, provenance: { tool: 'buddy_finish_turn', turnEnd: 'completed', flush: 'awaited', rootSessionMatched: true, toolCallId: 'call-1', rootCallId: 'call-1', promptSeq: 3, toolCallSeq: 5, toolResultSeq: 6, ptcDispatchSeq: null, turn: 1, turnEndSeq: 8, flushSeq: 9 } };
delete record.context;
delete record.executionWorkspace;

test('validates bounded input and honest completed/request dispositions', () => {
  assert.equal(validateTurnInput(input), input);
  assert.equal(validateTurnOutcome(outcome), outcome);
  const request = { summary: 'Need a decision', attempted: 'Inspected both APIs', neededWork: 'Choose one supported boundary', expectedArtifacts: [], acceptance: 'The selected API meets the task.' };
  assert.doesNotThrow(() => validateTurnOutcome({ ...outcome, disposition: 'attention', request }));
  assert.throws(() => validateTurnOutcome({ ...outcome, disposition: 'assistance' }), /request/);
  assert.throws(() => validateTurnOutcome({ ...outcome, request }), /request: null/);
  assert.throws(() => validateTurnOutcome({ ...outcome, taskId: 'forged' }), /unknown fields/);
  assert.doesNotThrow(() => validateTurnOutcome({ ...outcome, summary: '中'.repeat(3000) }));
  assert.throws(() => validateTurnOutcome({ ...outcome, summary: '中'.repeat(22000) }), /65536 bytes/);
  assert.throws(() => validateTurnOutcome({ ...outcome, disposition: 'attention', request: { ...request, summary: '中'.repeat(3000) } }), /8000 bytes/);
  assert.throws(() => validateTurnOutcome({ ...outcome, artifacts: Array(33).fill('x') }), /32/);
  assert.throws(() => validateTurnInput({ ...input, resumeMode: 'native-resume' }), /resumeMode/);
  assert.throws(() => validateTurnInput({ ...input, context: { secret: 'x'.repeat(262144) } }), /262144/);
});

test('record verification fences stale identities, missing PTC acceptance, and invented ordering', () => {
  assert.equal(validateTurnRecord(record, expected), record);
  for (const change of [{ generation: 2 }, { taskId: 'other' }, { promptSha256: sha256('other') }, { inputSha256: sha256('other') }]) {
    assert.throws(() => validateTurnRecord({ ...record, ...change }, expected), /does not match/);
  }
  assert.throws(() => validateTurnRecord({ ...record, provenance: { ...record.provenance, rootSessionMatched: false } }, expected), /evidence/);
  assert.throws(() => validateTurnRecord({ ...record, provenance: { ...record.provenance, flushSeq: 7 } }, expected), /out of order/);
  assert.throws(() => validateTurnRecord({ ...record, provenance: { ...record.provenance, toolCallId: 'call-1:ptc:1' } }, expected), /PTC/);
  assert.doesNotThrow(() => validateTurnRecord({ ...record, provenance: { ...record.provenance, toolCallId: 'call-1:ptc:1', toolResultSeq: 7, ptcDispatchSeq: 6 } }, expected));
});

test('attention may explicitly have no model suggestion without accepting malformed suggestions', () => {
  const request = { summary: 'Need a decision', attempted: 'Inspected the API', neededWork: 'Resolve ambiguity', expectedArtifacts: [], acceptance: 'A supported boundary is chosen.', suggestedProfileId: null };
  const attention = { ...outcome, disposition: 'attention', request };
  assert.equal(validateTurnOutcome(attention), attention);
  for (const suggestedProfileId of ['', ' ', 42, false, [], {}]) {
    assert.throws(() => validateTurnOutcome({ ...attention, request: { ...request, suggestedProfileId } }), /suggestedProfileId/);
  }
});

test('private files preserve raw-byte hashes, publish atomically, and refuse unsafe existing targets', () => {
  const dir = mkdtempSync(join(tmpdir(), 'buddy-turn-contract-'));
  try {
    const inputFile = join(dir, 'input.json');
    const bytes = `${JSON.stringify(input, null, 2)}\n`;
    writeFileSync(inputFile, bytes, { mode: 0o600 });
    assert.equal(readTurnInput(inputFile).inputSha256, sha256(bytes));
    const outputFile = join(dir, 'output.json');
    writeTurnRecord(outputFile, record);
    assert.deepEqual(readTurnRecord(outputFile, expected), record);
    assert.throws(() => writeTurnRecord(outputFile, { replaced: true }), /already exists/);
    assert.deepEqual(JSON.parse(readFileSync(outputFile)), record);
    const link = join(dir, 'link.json');
    symlinkSync(join(dir, 'missing.json'), link);
    assert.throws(() => turnOutputPath(link), /already exists/);
    assert.equal(existsSync(join(dir, 'missing.json')), false);
    chmodSync(inputFile, 0o644);
    assert.throws(() => readTurnInput(inputFile), /0600/);
    chmodSync(dir, 0o755);
    assert.throws(() => turnOutputPath(join(dir, 'unsafe.json')), /0700/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
