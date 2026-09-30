/** Shared validation and private file boundary for Buddy's version-1 turn protocol. */
import { createHash, randomUUID } from 'node:crypto';
import {
  closeSync, constants, fstatSync, fsyncSync, linkSync, lstatSync, openSync,
  readSync, realpathSync, unlinkSync, writeFileSync,
} from 'node:fs';
import { basename, dirname, isAbsolute, join } from 'node:path';

export const TURN_TOOL = 'buddy_finish_turn';
export const TURN_LIMITS = Object.freeze({ inputBytes: 262144, outcomeBytes: 65536, outputBytes: 98304, items: 32, textBytes: 8000, referenceBytes: 4096 });
export const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const hash = (value) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

function fields(value, required, optional = [], label = 'value') {
  if (!object(value)) throw new Error(`${label} must be an object`);
  if (required.some((key) => !Object.hasOwn(value, key))) throw new Error(`${label} is missing required fields`);
  if (Object.keys(value).some((key) => !required.includes(key) && !optional.includes(key))) throw new Error(`${label} has unknown fields`);
}

function text(value, label, maxBytes = TURN_LIMITS.textBytes) {
  if (typeof value !== 'string' || value.trim() === '' || value.includes('\0') || Buffer.byteLength(value) > maxBytes) {
    throw new Error(`${label} must be nonblank text of at most ${maxBytes} bytes without NUL`);
  }
}

function strings(value, label) {
  if (!Array.isArray(value) || value.length > TURN_LIMITS.items) throw new Error(`${label} must be an array of at most ${TURN_LIMITS.items} items`);
  value.forEach((item) => text(item, label, TURN_LIMITS.referenceBytes));
}

/** Reject non-JSON values and excessive nesting before serializing a bounded snapshot. */
function json(value, maxBytes, label) {
  const visit = (part, depth) => {
    if (depth > 16) throw new Error(`${label} is nested too deeply`);
    if (part === null || typeof part === 'string' || typeof part === 'boolean') return;
    if (typeof part === 'number' && Number.isFinite(part)) return;
    if (!object(part) && !Array.isArray(part)) throw new Error(`${label} must contain only JSON values`);
    for (const child of Object.values(part)) visit(child, depth + 1);
  };
  visit(value, 0);
  const serialized = JSON.stringify(value);
  if (Buffer.byteLength(serialized) > maxBytes) throw new Error(`${label} exceeds ${maxBytes} bytes`);
  return serialized;
}

export function validateTurnInput(input) {
  fields(input, ['version', 'taskId', 'attemptId', 'generation', 'turnId', 'resumeMode', 'previousSessionId', 'context', 'executionWorkspace'], [], 'turn input');
  if (input.version !== 1) throw new Error('turn input version must be 1');
  for (const key of ['taskId', 'attemptId', 'turnId']) text(input[key], key, 256);
  if (!Number.isSafeInteger(input.generation) || input.generation < 1) throw new Error('turn input generation must be a positive safe integer');
  if (!['initial', 'reconstructed-new-session'].includes(input.resumeMode)) throw new Error('turn input resumeMode must be initial or reconstructed-new-session');
  if (input.previousSessionId !== null) text(input.previousSessionId, 'previousSessionId', 256);
  if (input.resumeMode === 'initial' && input.previousSessionId !== null) throw new Error('an initial turn cannot name a previous session');
  if (!object(input.context) || !object(input.executionWorkspace)) throw new Error('turn input context and executionWorkspace must be objects');
  json(input, TURN_LIMITS.inputBytes, 'turn input');
  return input;
}

/** DSH 0.1.5-rc.1 supports oneOf and scalar types, but no min/max schema keywords. */
const stringArray = { type: 'array', items: { type: 'string' } };
export const TURN_OUTCOME_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['disposition', 'summary', 'remaining', 'decisions', 'artifacts', 'request'],
  properties: {
    disposition: { type: 'string', enum: ['completed', 'assistance', 'attention'] },
    summary: { type: 'string', description: 'Nonblank report; the entire serialized outcome must fit in 64 KiB of UTF-8.' },
    remaining: stringArray, decisions: stringArray,
    artifacts: { type: 'array', items: { oneOf: [{ type: 'string' }, { type: 'object', additionalProperties: true }] }, description: 'At most 32 file/commit references; these are claims for the Host to verify.' },
    request: { oneOf: [
      { type: 'null' },
      { type: 'object', additionalProperties: false, required: ['summary', 'attempted', 'neededWork', 'expectedArtifacts', 'acceptance'], properties: {
        summary: { type: 'string' }, attempted: { type: 'string' }, neededWork: { type: 'string' },
        expectedArtifacts: stringArray, acceptance: { type: 'string' },
        suggestedProfileId: { oneOf: [{ type: 'string' }, { type: 'null' }], description: 'Optional suggestion; null means no suggested profile.' },
      } },
    ] },
  },
};

export function validateTurnOutcome(outcome) {
  fields(outcome, ['disposition', 'summary', 'remaining', 'decisions', 'artifacts', 'request'], [], 'turn outcome');
  if (!['completed', 'assistance', 'attention'].includes(outcome.disposition)) throw new Error('invalid turn disposition');
  text(outcome.summary, 'summary', TURN_LIMITS.outcomeBytes);
  strings(outcome.remaining, 'remaining');
  strings(outcome.decisions, 'decisions');
  if (!Array.isArray(outcome.artifacts) || outcome.artifacts.length > TURN_LIMITS.items) throw new Error('artifacts must contain at most 32 references');
  for (const artifact of outcome.artifacts) {
    if (typeof artifact === 'string') text(artifact, 'artifact', TURN_LIMITS.referenceBytes);
    else if (!object(artifact) || Object.keys(artifact).length === 0) throw new Error('artifact must be a nonempty reference');
    json(artifact, TURN_LIMITS.referenceBytes, 'artifact');
  }
  if (outcome.disposition === 'completed') {
    if (outcome.request !== null) throw new Error('a completed outcome requires request: null');
  } else {
    const request = outcome.request;
    fields(request, ['summary', 'attempted', 'neededWork', 'expectedArtifacts', 'acceptance'], ['suggestedProfileId'], 'turn request');
    for (const key of ['summary', 'attempted', 'neededWork', 'acceptance']) text(request[key], `request.${key}`);
    strings(request.expectedArtifacts, 'request.expectedArtifacts');
    if (Object.hasOwn(request, 'suggestedProfileId') && request.suggestedProfileId !== null) text(request.suggestedProfileId, 'suggestedProfileId', 256);
  }
  json(outcome, TURN_LIMITS.outcomeBytes, 'turn outcome');
  return outcome;
}

export function validateTurnRecord(record, { input, inputSha256, promptSha256 }) {
  fields(record, ['version', 'taskId', 'attemptId', 'generation', 'turnId', 'resumeMode', 'previousSessionId', 'sessionId', 'promptSha256', 'inputSha256', 'outcome', 'provenance'], [], 'turn record');
  for (const key of ['version', 'taskId', 'attemptId', 'generation', 'turnId', 'resumeMode', 'previousSessionId']) {
    if (record[key] !== input[key]) throw new Error(`turn record ${key} does not match this attempt`);
  }
  text(record.sessionId, 'sessionId', 256);
  if (record.sessionId === record.previousSessionId) throw new Error('reconstructed execution must use a new session');
  if (!hash(record.inputSha256) || record.inputSha256 !== inputSha256) throw new Error('turn record inputSha256 does not match this attempt');
  if (!hash(record.promptSha256) || record.promptSha256 !== promptSha256) throw new Error('turn record promptSha256 does not match this run');
  validateTurnOutcome(record.outcome);
  const p = record.provenance;
  fields(p, ['tool', 'turnEnd', 'flush', 'rootSessionMatched', 'toolCallId', 'rootCallId', 'promptSeq', 'toolCallSeq', 'toolResultSeq', 'ptcDispatchSeq', 'turn', 'turnEndSeq', 'flushSeq'], [], 'turn provenance');
  if (p.tool !== TURN_TOOL || p.turnEnd !== 'completed' || p.flush !== 'awaited' || p.rootSessionMatched !== true) throw new Error('turn record lacks accepted root/turn/flush evidence');
  for (const key of ['toolCallId', 'rootCallId']) text(p[key], key, 512);
  for (const key of ['promptSeq', 'toolCallSeq', 'toolResultSeq', 'turn', 'turnEndSeq', 'flushSeq']) {
    if (!Number.isSafeInteger(p[key]) || p[key] < 0) throw new Error(`invalid provenance ${key}`);
  }
  if (p.turn < 1 || !(p.promptSeq < p.toolCallSeq && p.toolCallSeq < p.toolResultSeq && p.toolResultSeq < p.turnEndSeq && p.turnEndSeq < p.flushSeq)) throw new Error('turn provenance events are out of order');
  if (p.toolCallId === p.rootCallId) {
    if (p.ptcDispatchSeq !== null) throw new Error('native result must not claim PTC evidence');
  } else if (!Number.isSafeInteger(p.ptcDispatchSeq) || !(p.toolCallSeq < p.ptcDispatchSeq && p.ptcDispatchSeq < p.toolResultSeq)) throw new Error('nested result lacks ordered PTC evidence');
  json(record, TURN_LIMITS.outputBytes, 'turn record');
  return record;
}

function privateParent(file) {
  if (typeof file !== 'string' || !isAbsolute(file) || file.includes('\0')) throw new Error('turn file path must be absolute without NUL');
  const parent = realpathSync(dirname(file));
  const info = lstatSync(parent);
  if (!info.isDirectory() || info.uid !== process.getuid() || (info.mode & 0o077) !== 0) throw new Error('turn file parent must be an owner-private directory (0700)');
  return join(parent, basename(file));
}

/** Validate absence without following even a dangling symlink. Never overwrite an old receipt. */
export function turnOutputPath(file) {
  const resolved = privateParent(file);
  try { lstatSync(resolved); } catch (error) {
    if (error.code === 'ENOENT') return resolved;
    throw error;
  }
  throw new Error('turn output already exists; refusing to overwrite it');
}

function readPrivate(file, maxBytes) {
  const resolved = privateParent(file);
  const fd = openSync(resolved, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o077) !== 0) throw new Error('turn input/output must be an owner-private regular file (0600)');
    if (info.size < 1 || info.size > maxBytes) throw new Error(`turn file must contain 1-${maxBytes} bytes`);
    const buffer = Buffer.alloc(maxBytes + 1);
    let length = 0;
    while (length < buffer.length) {
      const count = readSync(fd, buffer, length, buffer.length - length, null);
      if (count === 0) break;
      length += count;
    }
    if (length > maxBytes) throw new Error(`turn file exceeds ${maxBytes} bytes`);
    const bytes = buffer.subarray(0, length);
    let value;
    try { value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
    catch { throw new Error('turn file must contain valid UTF-8 JSON'); }
    return { file: resolved, bytes, value };
  } finally { closeSync(fd); }
}

export function readTurnInput(file) {
  const loaded = readPrivate(file, TURN_LIMITS.inputBytes);
  return { inputFile: loaded.file, input: validateTurnInput(loaded.value), inputSha256: sha256(loaded.bytes) };
}

export function readTurnRecord(file, expected) {
  return validateTurnRecord(readPrivate(file, TURN_LIMITS.outputBytes).value, expected);
}

/** Flush an immutable 0600 file and publish it atomically without replacing any target. */
export function writeTurnRecord(file, record) {
  const resolved = turnOutputPath(file);
  const temporary = join(dirname(resolved), `.${basename(resolved)}.${randomUUID()}.tmp`);
  let created = false;
  try {
    const fd = openSync(temporary, 'wx', 0o600);
    created = true;
    try {
      writeFileSync(fd, `${json(record, TURN_LIMITS.outputBytes - 1, 'turn record')}\n`);
      fsyncSync(fd);
    } finally { closeSync(fd); }
    linkSync(temporary, resolved);
    const directory = openSync(dirname(resolved), constants.O_RDONLY);
    try { fsyncSync(directory); } finally { closeSync(directory); }
  } finally {
    if (created) {
      try { unlinkSync(temporary); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
  }
}
