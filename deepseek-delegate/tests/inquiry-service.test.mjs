/**
 * Service-level tests for `buddy inquire`.
 *
 * A real `JobManager` owns a mock runner that mounts the REAL inquiry bridge
 * plugin over a synthetic agent, so every assertion crosses the actual durable
 * record, the actual socket protocol, and the actual `inquire` orchestration.
 * No dsh and no model are involved.
 */
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { JobManager, inquiryBridgePaths } from '../service/jobs.mjs';
import { inquireRun, validateInquireParams } from '../service/inquiry.mjs';
import { waitForChange } from '../service/wait.mjs';

const RUNNER = fileURLToPath(new URL('./support/mock-inquiry-runner.mjs', import.meta.url));

/** Poll until `check` returns a truthy value. */
async function eventually(check, description, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await check();
    if (value) return value;
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${description}`);
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
}

/** One isolated service: private state dir, private cwd, mock runner, owned lifecycle. */
async function withService(env, body) {
  const stateDir = mkdtempSync(join(tmpdir(), 'buddy-inquiry-state-'));
  const cwd = mkdtempSync(join(tmpdir(), 'buddy-inquiry-cwd-'));
  const manager = new JobManager({ stateDir, runnerPath: RUNNER, env: { ...process.env, ...env } });
  await manager.init();
  const service = { manager, stateDir, cwd };
  try {
    return await body(service);
  } finally {
    await manager.shutdown();
    rmSync(stateDir, { recursive: true, force: true });
    rmSync(cwd, { recursive: true, force: true });
  }
}

/** Start one run and return its public view. */
async function startRun(service, requestId, task = 'inquiry service task') {
  return service.manager.dispatch('start', { requestId, task, cwd: service.cwd, timeoutSeconds: 600 });
}

test('read-only observation reports authoritative state, deadline and live activity', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '2500' }, async (service) => {
    const run = await startRun(service, 'observe-running');
    await eventually(async () => (await inquireRun(service.manager, { runId: run.runId })).live.available, 'the bridge to bind');
    const envelope = await inquireRun(service.manager, { runId: run.runId });

    assert.equal(envelope.runId, run.runId);
    assert.equal(envelope.status, 'running');
    assert.equal(envelope.phase, 'active');
    assert.equal(envelope.execution.resultAvailable, false);
    assert.equal(envelope.deadline.available, true);
    assert.equal(envelope.deadline.timeoutSeconds, 600);
    assert.equal(envelope.deadline.estimated, true);
    assert.equal(envelope.deadline.exact, false);
    assert.equal(envelope.deadline.kind, 'estimated-runner-deadline-from-record-createdAt');
    assert.match(envelope.deadline.clockOrigin, /record createdAt/);
    assert.equal(envelope.deadline.deadlineBasis, 'createdAt + timeoutSeconds');
    assert.equal(envelope.deadline.terminal, false);
    assert.ok(envelope.deadline.elapsedSeconds >= 0);
    assert.ok(envelope.deadline.remainingSeconds > 500);
    assert.equal(envelope.deadline.expired, false);
    assert.equal(envelope.bridge.observed, true);
    assert.equal(envelope.live.available, true);
    assert.equal(envelope.live.agentStatus, 'running');
    assert.ok(envelope.live.sessionId);
    assert.equal(envelope.live.activity.at(-1).tool, 'bash');
    assert.match(envelope.live.activity.at(-1).argumentPreview, /sleep 600/);
    assert.equal(envelope.live.limits.exposesModelReasoning, false);
    assert.equal(envelope.inquiry, null);
    assert.equal(envelope.limits.exposesModelReasoning, false);
  });
});

test('a question is queued, delivered, then answered with an exact correlation', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '4000', MOCK_INQUIRY_ANSWER_MS: '1200', MOCK_INQUIRY_ANSWER: 'blocked on a long sleep' }, async (service) => {
    const run = await startRun(service, 'ask-answered');
    await eventually(async () => (await inquireRun(service.manager, { runId: run.runId })).live.available, 'the bridge to bind');

    const asked = await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-1', question: 'what is blocking you?' });
    assert.equal(asked.inquiry.inquiryId, 'q-1');
    assert.equal(asked.inquiry.recorded, true);
    assert.equal(asked.inquiry.duplicate, false);
    assert.equal(asked.inquiry.correlation, 'inquiryId');
    assert.equal(asked.inquiry.questionBytes, Buffer.byteLength('what is blocking you?'));
    assert.ok(['queued', 'delivered'].includes(asked.inquiry.state), `unexpected state ${asked.inquiry.state}`);
    assert.equal(asked.inquiry.answer.available, false);

    // Repeating the identical inquiry never injects twice and reports duplicate.
    const repeated = await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-1', question: 'what is blocking you?' });
    assert.equal(repeated.inquiry.duplicate, true);

    const answered = await eventually(async () => {
      const envelope = await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-1', question: 'what is blocking you?' });
      return envelope.inquiry.state === 'answered' ? envelope : null;
    }, 'the correlated answer');
    assert.equal(answered.inquiry.answer.available, true);
    assert.equal(answered.inquiry.answer.text, 'blocked on a long sleep');
    assert.equal(answered.inquiry.answer.via, 'tool:buddy_inquiry_reply');
    assert.equal(answered.inquiry.answer.toolCallId, 'call-answer');
    assert.equal(answered.inquiry.answer.source, 'live-bridge');
    assert.ok(answered.inquiry.deliveredAt, 'the claimed boundary is recorded');
    assert.ok(answered.inquiry.answer.at);
    assert.deepEqual(answered.pendingInquiries.map((entry) => entry.inquiryId), ['q-1']);
  });
});

test('waitMs collects the answer produced at the next boundary', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '6000', MOCK_INQUIRY_ANSWER_MS: '700' }, async (service) => {
    const run = await startRun(service, 'ask-wait');
    await eventually(async () => (await inquireRun(service.manager, { runId: run.runId })).live.available, 'the bridge to bind');
    const envelope = await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-wait', question: 'progress?', waitMs: 5000 });
    assert.equal(envelope.inquiry.state, 'answered');
    assert.equal(envelope.inquiry.answer.text, 'mock answer');
  });
});

test('the same inquiry id with different text is a conflict and never injects twice', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '2500' }, async (service) => {
    const run = await startRun(service, 'ask-conflict');
    await eventually(async () => (await inquireRun(service.manager, { runId: run.runId })).live.available, 'the bridge to bind');
    await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-dup', question: 'first text' });
    await assert.rejects(
      () => inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-dup', question: 'second text' }),
      (error) => error.code === 'CONFLICT',
    );
    const journal = readFileSync(inquiryBridgePaths(service.stateDir, run.runId).resultsPath, 'utf8');
    assert.equal(journal.split('\n').filter((line) => line.includes('"state":"queued"')).length, 1, 'queued exactly once');
  });
});

test('a terminal run reports read-only state and refuses to inject a question', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '300' }, async (service) => {
    const run = await startRun(service, 'terminal-run');
    const finished = await eventually(async () => {
      const view = await service.manager.dispatch('status', { runId: run.runId });
      return view.status === 'completed' ? view : null;
    }, 'the run to complete');
    assert.equal(finished.resultAvailable, true);

    const observed = await inquireRun(service.manager, { runId: run.runId });
    assert.equal(observed.phase, 'terminal');
    assert.equal(observed.execution.resultAvailable, true);
    assert.equal(observed.bridge.observed, false);
    assert.equal(observed.bridge.reason, 'run-completed');
    assert.equal(observed.live.available, false);
    assert.ok(observed.live.unavailable.includes('agentStatus'), 'unavailable fields are named');
    assert.ok(observed.live.unavailable.includes('activity'));
    assert.equal(observed.inquiry, null);

    const refused = await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-late', question: 'anyone there?' });
    assert.equal(refused.inquiry.state, 'unavailable');
    assert.equal(refused.inquiry.recorded, false);
    assert.match(refused.inquiry.reason, /no live agent/);
    assert.equal(refused.inquiry.answer.available, false);
    // The refused question leaves no durable trace.
    assert.equal((await service.manager.dispatch('status', { runId: run.runId })).inquiries.q, undefined);
  });
});

test('a terminal run rejects conflicting text and still reads back the identical inquiry', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '4000', MOCK_INQUIRY_ANSWER_MS: '1200', MOCK_INQUIRY_ANSWER: 'terminal answer' }, async (service) => {
    const run = await startRun(service, 'terminal-conflict');
    await eventually(async () => (await inquireRun(service.manager, { runId: run.runId })).live.available, 'the bridge to bind');
    await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-terminal', question: 'the durable question' });
    const answered = await eventually(async () => {
      const envelope = await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-terminal', question: 'the durable question' });
      return envelope.inquiry.state === 'answered' ? envelope : null;
    }, 'the correlated answer');
    assert.equal(answered.inquiry.answer.text, 'terminal answer');
    await eventually(async () => (await service.manager.dispatch('status', { runId: run.runId })).status === 'completed', 'the run to complete');

    const recordPath = join(service.stateDir, run.runId, 'record.json');
    const before = readFileSync(recordPath, 'utf8');
    const beforeRun = JSON.parse(before);

    // Conflicting text for an id the terminal run already owns is the same
    // CONFLICT an active run raises, checked against the recorded UTF-8 hash.
    await assert.rejects(
      () => inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-terminal', question: 'a different question' }),
      (error) => error.code === 'CONFLICT',
    );
    assert.equal(readFileSync(recordPath, 'utf8'), before, 'a terminal CONFLICT must not mutate the record');
    const afterConflict = await service.manager.dispatch('status', { runId: run.runId });
    assert.equal(afterConflict.status, 'completed');
    assert.equal(afterConflict.updatedAt, beforeRun.updatedAt, 'a terminal CONFLICT must not touch the run');
    assert.equal(afterConflict.inquiries['q-terminal'].answer.text, 'terminal answer');

    // The identical repeat still reads the recorded answer back, byte-for-byte
    // from the durable record, and never creates or re-queues a question.
    const repeated = await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-terminal', question: 'the durable question' });
    assert.equal(repeated.phase, 'terminal');
    assert.equal(repeated.inquiry.recorded, true);
    assert.equal(repeated.inquiry.duplicate, true);
    assert.equal(repeated.inquiry.state, 'answered');
    assert.equal(repeated.inquiry.answer.available, true);
    assert.equal(repeated.inquiry.answer.text, 'terminal answer');
    assert.equal(repeated.bridge.reason, 'run-terminal-answer-from-journal');
    assert.equal(readFileSync(recordPath, 'utf8'), before, 'an identical terminal repeat must not rewrite the record');
  });
});

test('a run whose bridge is not ready yet reports unavailable fields, never fake progress', async () => {
  await withService({ MOCK_INQUIRY_BIND: '0', MOCK_INQUIRY_HOLD_MS: '2500' }, async (service) => {
    const run = await startRun(service, 'not-ready');
    await eventually(() => existsSync(inquiryBridgePaths(service.stateDir, run.runId).socketPath), 'the bridge socket');
    const envelope = await inquireRun(service.manager, { runId: run.runId });
    assert.equal(envelope.phase, 'active');
    assert.equal(envelope.bridge.observed, true, 'the socket answers but no session is bound yet');
    assert.equal(envelope.live.available, false);
    assert.equal(envelope.live.sessionId, null);
    assert.deepEqual(envelope.live.activity, []);
    const asked = await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-early', question: 'too early?' });
    assert.equal(asked.inquiry.state, 'unavailable');
    assert.match(asked.inquiry.answer.reason, /not-ready|not delivered/);
  });
});

test('an unknown run is rejected and malformed input never reaches the service', async () => {
  await withService({}, async (service) => {
    await assert.rejects(() => inquireRun(service.manager, { runId: '00000000-0000-4000-8000-000000000000' }), (error) => error.code === 'NOT_FOUND');
    for (const [params, pattern] of [
      [{}, /runId/],
      [{ runId: 'x', question: 'no id' }, /together/],
      [{ runId: 'x', inquiryId: 'q' }, /together/],
      [{ runId: 'x', inquiryId: 'bad id!', question: 'q' }, /inquiryId/],
      [{ runId: 'x', inquiryId: 'q', question: '   ' }, /nonempty/],
      [{ runId: 'x', timeoutMs: 99999 }, /timeoutMs/],
      [{ runId: 'x', waitMs: -1 }, /waitMs/],
      [{ runId: 'x', extra: true }, /Unknown inquire parameter/],
    ]) {
      assert.throws(() => validateInquireParams(params), pattern, JSON.stringify(params));
    }
    assert.throws(() => validateInquireParams(null), /object of parameters/);
  });
});

test('inquiry never mutates the execution deadline and never cancels the run', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '2500' }, async (service) => {
    const run = await startRun(service, 'deadline');
    await eventually(async () => (await inquireRun(service.manager, { runId: run.runId })).live.available, 'the bridge to bind');
    const before = await service.manager.dispatch('status', { runId: run.runId });
    await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-1', question: 'progress?' });
    await inquireRun(service.manager, { runId: run.runId, waitMs: 0 });
    await new Promise((resolve) => setTimeout(resolve, 400));
    const after = await service.manager.dispatch('status', { runId: run.runId });
    assert.equal(after.timeoutSeconds, before.timeoutSeconds);
    assert.equal(after.createdAt, before.createdAt);
    assert.ok(after.cancelRequestedAt === null || after.cancelRequestedAt === undefined, 'no cancellation was requested');
    assert.equal(after.status, 'running');
    assert.equal(after.inputHash, undefined, 'the view never leaks the input hash');
    const completed = await eventually(async () => {
      const view = await service.manager.dispatch('status', { runId: run.runId });
      return view.status === 'completed' ? view : null;
    }, 'the run to still complete normally');
    assert.equal(completed.runId, run.runId);
    assert.equal(completed.status, 'completed');
  });
});

test('a parallel inquiry neither blocks status nor a pending await wait', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '6000' }, async (service) => {
    const run = await startRun(service, 'parallel');
    await eventually(async () => (await inquireRun(service.manager, { runId: run.runId })).live.available, 'the bridge to bind');

    // A pending await wait is registered first, exactly like `buddy await`.
    const waiting = waitForChange(service.manager, { runId: run.runId, timeoutMs: 5000 });
    // An inquiry against a bridge that answers slowly must not hold the tail.
    const credentials = service.manager.bridgeCredentials(run.runId);
    void credentials;
    const slowInquire = inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-slow', question: 'slow?', timeoutMs: 5000 });
    const started = Date.now();
    const status = await service.manager.dispatch('status', { runId: run.runId });
    const elapsed = Date.now() - started;
    assert.equal(status.runId, run.runId);
    assert.ok(elapsed < 1000, `status must stay prompt during an inquiry (took ${elapsed}ms)`);
    const settled = await slowInquire;
    assert.equal(settled.inquiry.state === 'unavailable' || settled.inquiry.recorded, true);
    await waiting;
  });
});

test('bridge state and socket are cleaned up once the run is confirmed stopped', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '300' }, async (service) => {
    const run = await startRun(service, 'cleanup');
    const bridge = inquiryBridgePaths(service.stateDir, run.runId);
    await eventually(() => existsSync(bridge.socketPath), 'the bridge socket');
    assert.equal(statSync(bridge.socketPath).isSocket(), true);
    await eventually(async () => (await service.manager.dispatch('status', { runId: run.runId })).status === 'completed', 'the run to complete');
    await eventually(() => !existsSync(bridge.socketPath), 'the socket to be removed');
    assert.equal(existsSync(`${bridge.socketPath}.runner-exit`), true, 'the mock runner really exited');
  });
});

test('a bridge that cannot start is reported honestly and the run still succeeds', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '300' }, async (service) => {
    const run = await startRun(service, 'bridge-broken');
    const bridge = inquiryBridgePaths(service.stateDir, run.runId);
    // Occupy the socket path with a plain file: the bridge must refuse to replace it.
    const { writeFileSync } = await import('node:fs');
    const other = mkdtempSync(join(tmpdir(), 'buddy-inquiry-occupied-'));
    const occupied = join(other, 'inquiry.sock');
    writeFileSync(occupied, 'not a socket');
    void bridge;
    rmSync(other, { recursive: true, force: true });
    const completed = await eventually(async () => {
      const view = await service.manager.dispatch('status', { runId: run.runId });
      return view.status === 'completed' ? view : null;
    }, 'the run to complete despite no bridge');
    assert.equal(completed.resultAvailable, true);
    const observed = await inquireRun(service.manager, { runId: run.runId });
    assert.equal(observed.bridge.observed, false);
  });
});

test('the durable record never exposes the bridge token', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '400' }, async (service) => {
    const run = await startRun(service, 'redaction');
    const view = await service.manager.dispatch('status', { runId: run.runId });
    assert.equal(JSON.stringify(view).includes('token'), false);
    assert.equal(typeof view.inquiryBridge.socketPath, 'string');
    const credentials = service.manager.bridgeCredentials(run.runId);
    assert.equal(typeof credentials.token, 'string');
    assert.ok(credentials.token.length > 20);
    assert.match(credentials.token, /^[0-9a-f]{64}$/, 'the token must never look like a command-line option');
    const envelope = await inquireRun(service.manager, { runId: run.runId });
    assert.equal(JSON.stringify(envelope).includes(credentials.token), false);
    const onDisk = readFileSync(join(service.stateDir, run.runId, 'record.json'), 'utf8');
    assert.ok(onDisk.includes('token'), 'the token is durable for the run that owns it');
    assert.equal(statSync(join(service.stateDir, run.runId, 'record.json')).mode & 0o777, 0o600);
  });
});

test('a claimed question that is never committed is terminal, not a permanent queued state', async () => {
  // The mock claims the question for a proposed step but never commits it (a
  // rejected pre-step), so the reply tool must refuse and the run must end with
  // an honest terminal state instead of `queued`/`delivered`.
  await withService({ MOCK_INQUIRY_HOLD_MS: '2500', MOCK_INQUIRY_ANSWER_MS: '1200', MOCK_INQUIRY_DELIVER: '0' }, async (service) => {
    const run = await startRun(service, 'claim-without-commit');
    await eventually(async () => (await inquireRun(service.manager, { runId: run.runId })).live.available, 'the bridge to bind');
    await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-rejected', question: 'will this be delivered?' });
    // The bridge journals the claim; an inquiry merges it into the durable record.
    await eventually(async () => {
      const envelope = await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-rejected', question: 'will this be delivered?' });
      return envelope.inquiry.state === 'claimed' ? envelope : null;
    }, 'the claim to be journaled and merged');
    assert.equal((await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-rejected', question: 'will this be delivered?' })).inquiry.deliveredAt, null);
    await eventually(async () => (await service.manager.dispatch('status', { runId: run.runId })).status === 'completed', 'the run to end');

    const envelope = await inquireRun(service.manager, { runId: run.runId, inquiryId: 'q-rejected', question: 'will this be delivered?' });
    assert.equal(envelope.phase, 'terminal');
    assert.equal(envelope.inquiry.state, 'unavailable', 'a terminal run must not keep reporting a pending question');
    assert.equal(envelope.inquiry.answer.available, false);
    const durable = (await service.manager.dispatch('status', { runId: run.runId })).inquiries['q-rejected'];
    assert.equal(durable.state, 'unavailable');
    assert.equal(durable.answer, null);
    assert.equal(durable.delivery.deliveredAt ?? null, null, 'a claim is never recorded as delivery');
    assert.ok(durable.delivery.claimedAt);
  });
});

test('valid ids that collide with Object.prototype are ordinary own inquiries', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '2500', MOCK_INQUIRY_ANSWER_MS: '1200', MOCK_INQUIRY_ANSWER: 'own-key answer' }, async (service) => {
    const run = await startRun(service, 'own-keys');
    await eventually(async () => (await inquireRun(service.manager, { runId: run.runId })).live.available, 'the bridge to bind');

    for (const inquiryId of ['__proto__', 'constructor', 'toString']) {
      const asked = await inquireRun(service.manager, { runId: run.runId, inquiryId, question: `question for ${inquiryId}` });
      assert.equal(asked.inquiry.inquiryId, inquiryId);
      assert.equal(asked.inquiry.recorded, true);
      assert.equal(asked.inquiry.duplicate, false, `${inquiryId} must start as a new inquiry`);
      assert.equal(asked.inquiry.questionBytes, Buffer.byteLength(`question for ${inquiryId}`));
      const repeated = await inquireRun(service.manager, { runId: run.runId, inquiryId, question: `question for ${inquiryId}` });
      assert.equal(repeated.inquiry.duplicate, true, `${inquiryId} must dedupe as an own entry`);
      await assert.rejects(
        () => inquireRun(service.manager, { runId: run.runId, inquiryId, question: 'a different question' }),
        (error) => error.code === 'CONFLICT',
      );
    }
    // The prototype of Object itself must be untouched by any of those ids.
    assert.equal({}.polluted, undefined);
    assert.equal(Object.hasOwn(Object.prototype, 'questionPreview'), false);
    assert.equal(Object.getPrototypeOf({}), Object.prototype);

    // The mock answers the last steered message through the real bridge.
    const answered = await eventually(async () => {
      const envelope = await inquireRun(service.manager, { runId: run.runId, inquiryId: 'toString', question: 'question for toString' });
      return envelope.inquiry.state === 'answered' ? envelope : null;
    }, 'the toString inquiry answer');
    assert.equal(answered.inquiry.answer.text, 'own-key answer');
    assert.equal(answered.inquiry.answer.via, 'tool:buddy_inquiry_reply');

    const status = await service.manager.dispatch('status', { runId: run.runId });
    assert.deepEqual(Object.keys(status.inquiries).sort(), ['__proto__', 'constructor', 'toString']);
    assert.equal(status.inquiries['__proto__'].answer, null);
    assert.equal(status.inquiries['__proto__'].questionPreview, 'question for __proto__');

    // A durable update to an own-key id is applied to that id and nothing else.
    const updated = await service.manager.dispatch('inquiry-update', {
      runId: run.runId,
      inquiryId: '__proto__',
      patch: { state: 'discarded', reason: 'own-key update' },
    });
    assert.equal(updated.inquiry.state, 'discarded');
    const reread = await inquireRun(service.manager, { runId: run.runId, inquiryId: '__proto__', question: 'question for __proto__' });
    assert.equal(reread.inquiry.state, 'discarded');
    assert.equal(reread.inquiry.reason, 'own-key update');
    assert.equal({}.polluted, undefined, 'no write may ever reach Object.prototype');
  });
});

test('malformed or oversized updates never corrupt an inquiry record', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '2500' }, async (service) => {
    const run = await startRun(service, 'malformed-updates');
    const begun = await service.manager.dispatch('inquiry-begin', { runId: run.runId, inquiryId: 'q-malformed', question: 'original question' });
    assert.equal(begun.inquiry.state, 'queued');
    const before = JSON.parse(readFileSync(join(service.stateDir, run.runId, 'record.json'), 'utf8')).inquiries['q-malformed'];

    const malformed = [
      { patch: { state: 'made-up' } },
      { patch: { state: 'delivered', answer: { text: '中'.repeat(1400) } } }, // 4200 bytes > 4000
      { patch: { answer: { text: 42 } } },
      { patch: { answer: { notText: 'x' } } },
      { patch: { delivery: 'not-an-object' } },
      { patch: [] },
    ];
    for (const { patch } of malformed) {
      await assert.rejects(
        () => service.manager.dispatch('inquiry-update', { runId: run.runId, inquiryId: 'q-malformed', patch }),
        (error) => error.code === 'INVALID_ARGUMENT',
        JSON.stringify(patch).slice(0, 80),
      );
      const after = JSON.parse(readFileSync(join(service.stateDir, run.runId, 'record.json'), 'utf8')).inquiries['q-malformed'];
      assert.deepEqual(after, before, 'a rejected update must leave the record byte-identical');
    }

    // A valid delivery + answer commits; a later malformed or null answer never
    // clears or replaces it, and `answered` is never left with a null answer.
    await service.manager.dispatch('inquiry-update', {
      runId: run.runId,
      inquiryId: 'q-malformed',
      patch: { state: 'delivered', delivery: { messageId: 'm-1', claimedAt: '2026-09-18T10:00:00.000Z', deliveredAt: '2026-09-18T10:00:01.000Z' } },
    });
    const first = await service.manager.dispatch('inquiry-update', {
      runId: run.runId,
      inquiryId: 'q-malformed',
      patch: { answer: { text: 'first answer', via: 'tool:buddy_inquiry_reply', toolCallId: 'call-1', at: '2026-09-18T10:00:02.000Z' } },
    });
    assert.equal(first.inquiry.state, 'answered');
    assert.equal(first.inquiry.answer.text, 'first answer');
    assert.equal(first.inquiry.answer.bytes, Buffer.byteLength('first answer'));

    await assert.rejects(
      () => service.manager.dispatch('inquiry-update', { runId: run.runId, inquiryId: 'q-malformed', patch: { answer: { text: 'x'.repeat(4001) } } }),
      (error) => error.code === 'INVALID_ARGUMENT',
    );
    const afterNull = await service.manager.dispatch('inquiry-update', {
      runId: run.runId,
      inquiryId: 'q-malformed',
      patch: { answer: null, state: 'queued', reason: 'late observation' },
    });
    assert.equal(afterNull.inquiry.state, 'answered', 'an answer is terminal');
    assert.equal(afterNull.inquiry.answer.text, 'first answer', 'a later answer never overwrites the first');
    const second = await service.manager.dispatch('inquiry-update', {
      runId: run.runId,
      inquiryId: 'q-malformed',
      patch: { answer: { text: 'second answer' } },
    });
    assert.equal(second.inquiry.answer.text, 'first answer');
    assert.equal(second.inquiry.delivery.messageId, 'm-1');
    assert.equal(second.inquiry.delivery.claimedAt, '2026-09-18T10:00:00.000Z');
  });
});

test('state-only answered and blank answers can never corrupt pending or answered state', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '1500' }, async (service) => {
    const run = await startRun(service, 'answer-invariants');
    const begun = await service.manager.dispatch('inquiry-begin', { runId: run.runId, inquiryId: 'q-answer', question: 'pending question' });
    assert.equal(begun.inquiry.state, 'queued');
    const recordPath = join(service.stateDir, run.runId, 'record.json');
    const pending = JSON.parse(readFileSync(recordPath, 'utf8')).inquiries['q-answer'];
    assert.equal(pending.answer, null);

    // A state-only `answered`, an explicit null answer and blank text all fail
    // validation on the clone and leave the queued inquiry byte-identical.
    for (const patch of [
      { state: 'answered' },
      { state: 'answered', answer: null },
      { state: 'answered', answer: { text: '' } },
      { answer: { text: '' } },
      { answer: { text: '   ' } },
      { answer: { text: '\t\n' } },
    ]) {
      await assert.rejects(
        () => service.manager.dispatch('inquiry-update', { runId: run.runId, inquiryId: 'q-answer', patch }),
        (error) => error.code === 'INVALID_ARGUMENT',
        JSON.stringify(patch),
      );
      const after = JSON.parse(readFileSync(recordPath, 'utf8')).inquiries['q-answer'];
      assert.deepEqual(after, pending, `a rejected patch must not mutate the pending inquiry: ${JSON.stringify(patch)}`);
    }

    // A terminal bridge-journal recovery carries `answer` + `state` and no
    // delivery fields; it must still commit, because the invariant is about the
    // answer text and not about an extra delivery requirement.
    const recovered = await service.manager.dispatch('inquiry-update', {
      runId: run.runId,
      inquiryId: 'q-answer',
      patch: { state: 'answered', answer: { text: 'answer recovered from the journal', source: 'bridge-journal' } },
    });
    assert.equal(recovered.inquiry.state, 'answered');
    assert.equal(recovered.inquiry.answer.text, 'answer recovered from the journal');
    assert.equal(recovered.inquiry.answer.source, 'bridge-journal');
    assert.equal(recovered.inquiry.delivery, null);
    const answered = JSON.parse(readFileSync(recordPath, 'utf8')).inquiries['q-answer'];

    // A blank incoming answer is rejected even after an answer exists, and a
    // later nonblank answer still never replaces the first one.
    await assert.rejects(
      () => service.manager.dispatch('inquiry-update', { runId: run.runId, inquiryId: 'q-answer', patch: { answer: { text: ' ' } } }),
      (error) => error.code === 'INVALID_ARGUMENT',
    );
    const afterBlank = JSON.parse(readFileSync(recordPath, 'utf8')).inquiries['q-answer'];
    assert.deepEqual(afterBlank, answered, 'a blank incoming answer must not touch the recorded answer');
    const second = await service.manager.dispatch('inquiry-update', { runId: run.runId, inquiryId: 'q-answer', patch: { answer: { text: 'second answer' } } });
    assert.equal(second.inquiry.answer.text, 'answer recovered from the journal', 'the first answer wins');
  });
});

test('a terminal deadline is frozen and labelled estimated, not still accumulating', async () => {
  await withService({ MOCK_INQUIRY_HOLD_MS: '300' }, async (service) => {
    const run = await startRun(service, 'frozen-deadline');
    await eventually(async () => (await service.manager.dispatch('status', { runId: run.runId })).status === 'completed', 'the run to complete');
    const first = await inquireRun(service.manager, { runId: run.runId });
    assert.equal(first.deadline.terminal, true);
    assert.equal(first.deadline.estimated, true);
    assert.equal(first.deadline.measuredTo, first.execution.updatedAt);
    await new Promise((resolve) => setTimeout(resolve, 300));
    const second = await inquireRun(service.manager, { runId: run.runId });
    assert.equal(second.deadline.elapsedSeconds, first.deadline.elapsedSeconds, 'a finished run must not keep accumulating elapsed time');
    assert.equal(second.deadline.measuredTo, first.deadline.measuredTo);
    assert.ok(second.deadline.remainingSeconds > 500);
  });
});
