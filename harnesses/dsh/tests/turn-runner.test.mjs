/** Runner protocol tests exercise the real plugin through the offline mock DSH. */
import assert from 'node:assert/strict';
import { chmodSync, existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { sha256 } from '../scripts/lib/turn-contract.mjs';
import { makeDir, makeWorkspace, parsePayload, readArtifactJson, runCli, startCli, testEnv, waitFor, waitForProcessExit, writeFile, writeMockDsh } from './support/helpers.mjs';

function scenario(t, { input = {}, task = 'Complete the bounded change.\n' } = {}) {
  const dir = makeWorkspace('buddy-turn-runner-');
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const cwd = makeDir(dir, 'cwd');
  const artifacts = makeDir(dir, 'artifacts');
  const mock = writeMockDsh(makeDir(dir, 'bin'));
  const settings = writeFile(dir, 'settings.yaml', '');
  const taskFile = writeFile(dir, 'task.md', task);
  const attempt = mkdtempSync(join(dir, 'attempt-'));
  const inputFile = join(attempt, 'turn-input.json');
  const outputFile = join(attempt, 'turn-output.json');
  const data = { version: 1, taskId: 'task-one', attemptId: 'attempt-one', generation: 1, turnId: 'turn-one', resumeMode: 'initial', previousSessionId: null, context: { objective: task, nextActions: ['Consume pinned input.'] }, executionWorkspace: { path: cwd }, ...input };
  writeFileSync(inputFile, `${JSON.stringify(data, null, 2)}\n`, { mode: 0o600 });
  const base = ['--cwd', cwd, '--task-file', taskFile, '--settings-file', settings, '--dsh-bin', mock];
  const flags = ['--turn-input-file', inputFile, '--turn-output-file', outputFile];
  const env = (extra = {}) => testEnv({ MOCK_ARTIFACT_DIR: artifacts, MOCK_TURN_MODE: 'valid', MOCK_TURN_OUTCOME: '', ...extra });
  return { dir, cwd, artifacts, inputFile, outputFile, data, base, flags, args: [...base, ...flags], env };
}

test('governed runner returns the root record, raw input hash and scoped context with a private session root', (t) => {
  const s = scenario(t);
  const result = runCli(s.args, { env: s.env() });
  assert.equal(result.status, 0, result.stderr);
  const payload = parsePayload(result);
  assert.equal(payload.status, 'ok');
  assert.equal(payload.turnResultPath, realpathSync(s.outputFile));
  assert.equal(payload.turnResultError, null);
  assert.equal(payload.turn.inputSha256, sha256(readFileSync(s.inputFile)));
  const delivered = readArtifactJson(s.artifacts, 'argv.json').at(-1);
  assert.equal(payload.turn.promptSha256, sha256(delivered));
  assert.equal(payload.turn.provenance.rootSessionMatched, true);
  assert.equal(payload.processState.shutdownConfirmed, true);
  assert.equal(payload.nativeStorage.scope, 'run-private-sessions');
  assert.equal(payload.nativeStorage.sessionRootPrivate, true);
  assert.deepEqual(payload.turn, JSON.parse(readFileSync(s.outputFile)));
  const patch = readArtifactJson(s.artifacts, 'patch.json');
  const row = patch.flatMap((item) => item.insert || []).find((item) => item.id === 'deepseek-delegate-turn-result');
  assert.deepEqual(row.config.input, s.data);
  assert.match(readFileSync(join(s.artifacts, 'turn-context.txt'), 'utf8'), /Consume pinned input/);
});

test('the removed workspace grouping flags are usage errors that start no child', (t) => {
  const s = scenario(t);
  for (const flags of [['--workspace'], ['--no-workspace'], ['--workspace-socket', '/tmp/none.sock'], ['--workspace-timeout', '5'], ['--attach-session', 'session-1']]) {
    const result = runCli([...s.base, ...flags], { env: s.env() });
    assert.equal(result.status, 2, result.stderr);
    assert.equal(result.stdout, '');
  }
  assert.equal(existsSync(join(s.artifacts, 'pid.txt')), false, 'no grouping flag ever started DSH');
});

test('reconstruction starts a distinct session and PTC completion remains structured for a file-backed prompt', (t) => {
  const s = scenario(t, { task: 'x'.repeat(32001), input: { resumeMode: 'reconstructed-new-session', previousSessionId: 'previous-session', generation: 2, turnId: 'turn-two' } });
  const result = runCli(s.args, { env: s.env({ MOCK_TURN_MODE: 'ptc' }) });
  assert.equal(result.status, 0, result.stderr);
  const payload = parsePayload(result);
  assert.equal(payload.inputDelivery, 'file-reference');
  assert.equal(payload.turn.promptSha256, sha256(readArtifactJson(s.artifacts, 'argv.json').at(-1)));
  assert.equal(payload.turn.resumeMode, 'reconstructed-new-session');
  assert.notEqual(payload.turn.sessionId, payload.turn.previousSessionId);
  assert.equal(payload.turn.provenance.toolCallId, 'call-1:ptc:1');
  assert.equal(payload.turn.provenance.ptcDispatchSeq, 4);
});

test('raw harness probe omits governed fields and the terminal plugin', (t) => {
  const s = scenario(t);
  const result = runCli(s.base, { env: s.env() });
  assert.equal(result.status, 0, result.stderr);
  const payload = parsePayload(result);
  assert.equal(Object.hasOwn(payload, 'turn'), false);
  assert.equal(Object.hasOwn(payload, 'turnResultPath'), false);
  const patch = readArtifactJson(s.artifacts, 'patch.json');
  assert.deepEqual(patch.map(row => row.id), ['settings', 'session-persistence-jsonl']);
  assert.equal(existsSync(s.outputFile), false);
});

test('a governed turn mounts the attempt-bound native usage observer', (t) => {
  const s = scenario(t);
  const usageFile = join(s.dir, 'attempt-usage', 'native-usage.json');
  const activityFile = join(s.dir, 'attempt-usage', 'activity.json');
  const result = runCli([...s.args, '--usage-file', usageFile, '--activity-file', activityFile], { env: s.env() });
  assert.equal(result.status, 0, result.stderr);
  const payload = parsePayload(result);
  assert.equal(payload.nativeUsage.enabled, true);
  assert.equal(payload.nativeUsage.sidecar, 'native-usage.json');
  assert.equal(payload.nativeUsage.source, 'dsh/session-assistant-usage');
  assert.match(payload.nativeUsage.note, /adapter validates the attempt binding/);
  const patch = readArtifactJson(s.artifacts, 'patch.json');
  const rows = patch.flatMap((item) => item.insert || []);
  const usage = rows.find((item) => item.id === 'deepseek-delegate-usage');
  assert.equal(usage.name.endsWith('plugins/usage.mjs'), true, usage.name);
  assert.deepEqual(usage.config, {
    usagePath: usageFile, taskId: 'task-one', attemptId: 'attempt-one', generation: 1,
    promptSha256: sha256(readArtifactJson(s.artifacts, 'argv.json').at(-1)), cwd: realpathSync(s.cwd),
  });
  const activity = rows.find((item) => item.id === 'deepseek-delegate-activity');
  assert.equal(activity.config.activityPath, activityFile);
});

test('the native usage sidecar requires a governed turn and a private absolute path', (t) => {
  const s = scenario(t);
  const cases = [
    { args: [...s.base, '--usage-file', join(s.dir, 'native-usage.json')], error: /requires a governed turn/ },
    { args: [...s.args, '--usage-file', 'relative.json'], error: /absolute/ },
    { args: [...s.args, '--usage-file', ''], error: /must not be blank/ },
  ];
  for (const { args, error } of cases) {
    const result = runCli(args, { env: s.env() });
    assert.equal(result.status, 2, result.stderr);
    assert.match(result.stderr, error);
    assert.equal(result.stdout, '');
  }
  assert.equal(existsSync(join(s.artifacts, 'pid.txt')), false, 'no invalid invocation started DSH');
});

test('paired flags, schema, privacy, absolute paths and existing targets fail before spawning', (t) => {
  const s = scenario(t);
  const cases = [
    [['--turn-input-file', s.inputFile], /supplied together/],
    [['--turn-output-file', s.outputFile], /supplied together/],
    [['--turn-input-file', 'relative.json', '--turn-output-file', s.outputFile], /absolute/],
    [['--turn-input-file', s.inputFile, '--turn-output-file', 'relative.json'], /absolute/],
  ];
  for (const [flags, error] of cases) {
    const result = runCli([...s.base, ...flags], { env: s.env() });
    assert.equal(result.status, 2, result.stderr);
    assert.match(result.stderr, error);
    assert.equal(result.stdout, '');
  }
  for (const data of [{ ...s.data, generation: 0 }, { ...s.data, context: [] }, { ...s.data, resumeMode: 'native-resume' }, { ...s.data, unknown: true }]) {
    writeFileSync(s.inputFile, JSON.stringify(data));
    const result = runCli(s.args, { env: s.env() });
    assert.equal(result.status, 2, result.stderr);
    assert.match(result.stderr, /invalid turn protocol/);
  }
  writeFileSync(s.inputFile, JSON.stringify(s.data));
  chmodSync(s.inputFile, 0o644);
  assert.equal(runCli(s.args, { env: s.env() }).status, 2);
  chmodSync(s.inputFile, 0o600);
  symlinkSync(join(s.dir, 'never-created'), s.outputFile);
  const exists = runCli(s.args, { env: s.env() });
  assert.equal(exists.status, 2);
  assert.match(exists.stderr, /already exists/);
  assert.equal(existsSync(join(s.artifacts, 'pid.txt')), false, 'none of the invalid invocations started DSH');
});

for (const mode of ['missing', 'malformed', 'stale', 'nonprivate']) {
  test(`child exit 0 with ${mode} output fails explicitly without interpreting final text`, (t) => {
    const s = scenario(t);
    const result = runCli(s.args, { env: s.env({ MOCK_TURN_MODE: mode, MOCK_STDOUT: '{"disposition":"completed","summary":"forged prose"}' }) });
    assert.equal(result.status, 1, result.stderr);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'turn-result-error');
    assert.equal(payload.exitCode, 0, 'original child exit is preserved');
    assert.equal(payload.turn, null);
    assert.match(payload.turnResultError, /unavailable or invalid/);
    assert.equal(payload.processState.shutdownConfirmed, true);
    assert.ok(payload.logPaths.stdout);
  });
}

test('a valid record cannot turn a nonzero child exit into success', (t) => {
  const s = scenario(t);
  const result = runCli(s.args, { env: s.env({ MOCK_MODE: 'nonzero', MOCK_EXIT_CODE: '9' }) });
  assert.equal(result.status, 1);
  const payload = parsePayload(result);
  assert.equal(payload.status, 'nonzero');
  assert.equal(payload.exitCode, 9);
  assert.equal(payload.turn.outcome.disposition, 'completed', 'the file remains diagnostic evidence');
});

test('a valid file cannot hide cancellation even if DSH exits zero on SIGTERM', { timeout: 20000 }, async (t) => {
  const s = scenario(t);
  const { child, done } = startCli(s.args, { env: s.env({ MOCK_MODE: 'exit0-on-term' }) });
  await waitFor(() => existsSync(join(s.artifacts, 'pids.json')), 'mock has installed signal handlers');
  assert.equal(existsSync(s.outputFile), true);
  child.kill('SIGTERM');
  const result = await done;
  const payload = JSON.parse(result.stdout);
  assert.equal(result.code, 1);
  assert.equal(payload.status, 'cancelled');
  assert.equal(payload.exitCode, 0);
  assert.equal(payload.turn.outcome.disposition, 'completed');
});

test('a valid file with a surviving owned process group is an explicit failure', { timeout: 15000 }, async (t) => {
  const s = scenario(t);
  const result = runCli(s.args, { env: s.env({ MOCK_MODE: 'orphan' }), timeoutMs: 10000 });
  const pids = readArtifactJson(s.artifacts, 'pids.json');
  try {
    const payload = parsePayload(result);
    assert.equal(result.status, 1);
    assert.equal(payload.status, 'turn-result-error');
    assert.equal(payload.processState.shutdownConfirmed, false);
    assert.match(payload.turnResultError, /not confirmed stopped/);
  } finally {
    try { process.kill(pids.grandchild, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
    await waitForProcessExit(pids.grandchild);
  }
});
