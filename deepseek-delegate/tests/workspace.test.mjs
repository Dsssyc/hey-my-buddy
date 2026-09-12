/**
 * CLI behavior for workspace grouping and session attachment.
 *
 * Every test runs `scripts/run.mjs` as a child process against the mock dsh and
 * the local authenticated web fixture. No real dsh web service, model, external network,
 * credential, or dsh storage file is touched: the fixture is an in-process HTTP
 * server on an ephemeral loopback port, and the launch token is a test constant.
 */
import assert from 'node:assert/strict';
import { chmodSync, existsSync, mkdirSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { after, before, describe, test } from 'node:test';
import {
  makeDir, makeWorkspace, modeOf, parsePayload, readArtifact, readArtifactJson, readText,
  startCli, testEnv, waitFor, waitForProcessExit, writeFile, writeMockDsh,
} from './support/helpers.mjs';
import { startMockWeb } from './support/mock-web.mjs';

// Keep the in-process HTTP fixture responsive while the CLI awaits its replies.
async function runHttpCli(args, options) {
  const { child, done } = startCli(args, options);
  const deadline = setTimeout(() => child.kill('SIGTERM'), 30000);
  try {
    const result = await done;
    return { ...result, status: result.code };
  } finally {
    clearTimeout(deadline);
  }
}

const SESSION_ID = 'session-mock-0001';
const DEFAULT_ROUTE = { provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'max' };

let root;
before(() => {
  root = makeWorkspace('deepseek-delegate-workspace-');
});
after(() => {
  rmSync(root, { recursive: true, force: true });
});

/** One runnable scenario: private dirs, mock dsh, settings/task files, web fixture. */
async function scenario(name, options = {}) {
  const dir = makeDir(root, name);
  const cwd = options.cwdPath ?? makeDir(dir, 'cwd');
  if (!existsSync(cwd)) mkdirSync(cwd, { recursive: true });
  const artifacts = makeDir(dir, 'artifacts');
  const mock = options.mock ?? writeMockDsh(makeDir(dir, 'bin'));
  const settingsFile = writeFile(dir, 'settings.yaml', '');
  const taskFile = writeFile(dir, 'task.md', options.task ?? 'Do the bounded thing.\n');
  const marker = join(dir, 'child-exit-marker.txt');
  const webOptions = typeof options.web === 'function' ? options.web({ cwd: realpathSync(cwd) }) : (options.web ?? {});
  const fixture = await startMockWeb({ ...webOptions, childExitMarker: marker, childPidFile: join(artifacts, 'pid.txt') });
  const urlFile = join(dir, 'web-url.txt');
  writeFileSync(urlFile, (options.urlLine ?? (() => `${fixture.launchUrl}\n`))(fixture));
  const urlArgs = options.urlArgs ?? ['--dsh-web-url-file', urlFile];
  const args = options.attach !== undefined
    ? ['--cwd', cwd, '--attach-session', options.attach, ...urlArgs]
    : ['--cwd', cwd, '--task-file', taskFile, '--settings-file', settingsFile, '--dsh-bin', mock, ...urlArgs];
  const env = testEnv({
    MOCK_ARTIFACT_DIR: artifacts,
    MOCK_SESSION_ID: options.sessionId ?? SESSION_ID,
    MOCK_EXIT_MARKER: marker,
    XDG_CONFIG_HOME: makeDir(dir, 'xdg'),
    ...(options.mockEnv ?? {}),
    ...(options.env ?? {}),
  });
  return {
    dir,
    cwd,
    canonicalCwd: realpathSync(cwd),
    artifacts,
    mock,
    settingsFile,
    taskFile,
    urlFile,
    marker,
    fixture,
    args: [...args, ...(options.extraArgs ?? [])],
    env,
  };
}

function assertOneJsonLine(stdout) {
  assert.equal(stdout.trim().split('\n').length, 1, `stdout was not one line: ${stdout}`);
}

describe('workspace grouping after a run', async () => {
  test('does not adopt while a member of the owned process group survives', async (t) => {
    const s = await scenario('surviving-process-group', { mockEnv: { MOCK_MODE: 'orphan' } });
    t.after(() => s.fixture.close());
    t.after(async () => {
      if (!existsSync(join(s.artifacts, 'pids.json'))) return;
      const { grandchild } = readArtifactJson(s.artifacts, 'pids.json');
      if (grandchild) {
        try { process.kill(grandchild, 'SIGKILL'); } catch { /* already gone */ }
        await waitForProcessExit(grandchild);
      }
    });
    const result = await runHttpCli(s.args, { env: s.env });
    assert.equal(result.status, 1, result.stderr);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'ok');
    assert.equal(payload.workspace.bound, false);
    assert.match(payload.workspace.error, /not confirmed stopped/);
    assert.equal(s.fixture.rpcRequests('session/create').length, 0);
  });

  test('groups the captured root session into its canonical workspace after the child exits', async (t) => {
    const s = await scenario('group-ok');
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 0, result.stderr);
    assertOneJsonLine(result.stdout);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'ok');
    assert.equal(payload.mode, 'run');
    assert.equal(payload.cwd, s.canonicalCwd);
    assert.deepEqual(payload.workspace, {
      enabled: true,
      bound: true,
      id: 'workspace-1',
      path: s.canonicalCwd,
      sessionId: SESSION_ID,
    });

    // Preflight registration, then one adoption, then a membership read-back.
    const creates = s.fixture.rpcRequests('workspace/create');
    assert.equal(creates.length, 2, 'one preflight registration and one verified read-back');
    assert.equal(creates[0].payload.path, s.canonicalCwd);
    assert.equal(creates[0].authenticated, true);
    assert.equal(creates[0].envelope, true);
    assert.equal(creates[0].rpcMethodMatches, true);
    const adoptions = s.fixture.rpcRequests('session/create');
    assert.equal(adoptions.length, 1);
    assert.deepEqual(adoptions[0].payload, { sessionId: SESSION_ID, workspaceId: 'workspace-1' });
    assert.equal(adoptions[0].childExited, true, 'adoption happens only after the owned child exited');

    // The observer patch row points at the shipped plugin and a private capture.
    const patch = readArtifactJson(s.artifacts, 'patch.json');
    assert.equal(patch.length, 2);
    const insert = patch[1].insert[0];
    assert.equal(insert.id, 'deepseek-delegate-session-capture');
    assert.ok(insert.name.endsWith('plugins/session-capture.mjs'), insert.name);
    assert.equal(insert.config.cwd, s.canonicalCwd);
    assert.equal(insert.config.capturePath, payload.logPaths.capture);
    assert.equal(existsSync(insert.config.capturePath), true);
    assert.equal(modeOf(insert.config.capturePath), '600');
    assert.equal(JSON.parse(readText(insert.config.capturePath)).sessionId, SESSION_ID);
    assert.equal(readArtifact(s.artifacts, 'cwd.txt'), s.canonicalCwd);
  });

  test('reuses an existing workspace registration without creating a duplicate', async (t) => {
    const s = await scenario('group-reuse', {
      web: ({ cwd }) => ({ workspaces: [{ workspaceId: 'workspace-existing', path: cwd, sessionIds: [] }] }),
    });
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 0, result.stderr);
    const payload = parsePayload(result);
    assert.equal(payload.workspace.bound, true);
    assert.equal(payload.workspace.id, 'workspace-existing');
    assert.equal(payload.workspace.path, s.canonicalCwd);
    const creates = s.fixture.rpcRequests('workspace/create');
    assert.equal(creates.length, 2);
    assert.deepEqual([...new Set(creates.map((entry) => entry.payload.path))], [s.canonicalCwd]);
  });

  test('canonicalizes --cwd through a symlink alias before running and grouping', async (t) => {
    const dir = makeDir(root, 'group-alias');
    const realCwd = makeDir(dir, 'real-cwd');
    const alias = join(dir, 'alias-cwd');
    symlinkSync(realCwd, alias);
    const s = await scenario('group-alias-run', { cwdPath: alias });
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 0, result.stderr);
    const payload = parsePayload(result);
    assert.equal(payload.cwd, realpathSync(realCwd));
    assert.equal(payload.workspace.path, realpathSync(realCwd));
    assert.equal(s.fixture.rpcRequests('workspace/create')[0].payload.path, realpathSync(realCwd));
    assert.equal(readArtifact(s.artifacts, 'cwd.txt'), realpathSync(realCwd));
  });

  test('a nonzero child still preserves its outcome while grouping succeeds', async (t) => {
    const s = await scenario('group-nonzero', { mockEnv: { MOCK_MODE: 'nonzero', MOCK_EXIT_CODE: '4' } });
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 1);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'nonzero');
    assert.equal(payload.exitCode, 4);
    assert.equal(payload.workspace.bound, true);
    assert.equal(payload.workspace.sessionId, SESSION_ID);
  });

  test('cancellation keeps its status while the captured session is still grouped', { timeout: 30000 }, async (t) => {
    const s = await scenario('group-cancel', { mockEnv: { MOCK_MODE: 'hang' } });
    t.after(() => s.fixture.close());
    const { child, done } = startCli(s.args, { env: s.env });
    await waitFor(() => existsSync(join(s.artifacts, 'pids.json')), 'mock dsh to record its pids');
    child.kill('SIGTERM');
    const result = await done;

    assert.equal(result.code, 1);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'cancelled');
    assert.equal(payload.workspace.bound, true);
    assert.equal(payload.workspace.sessionId, SESSION_ID);
    const adoptions = s.fixture.rpcRequests('session/create');
    assert.equal(adoptions.length, 1);
    assert.equal(adoptions[0].childExited, true);
  });
});

describe('workspace preflight failures (no child is ever started)', async () => {
  test('a bare URL without a launch token fails before spawning dsh', async (t) => {
    const s = await scenario('preflight-bare', { urlLine: (fixture) => `${fixture.url}\n` });
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 2);
    assert.equal(result.stdout, '');
    assert.match(result.stderr, /no launch token/);
    assert.equal(existsSync(join(s.artifacts, 'argv.json')), false, 'no child was spawned');
    assert.equal(s.fixture.requests.length, 0, 'a tokenless URL is rejected without a request');
  });

  test('a rejected or expired token fails before spawning dsh', async (t) => {
    const s = await scenario('preflight-rejected', {
      web: { token: 'the-current-process-token' },
      urlLine: (fixture) => `${fixture.url}?token=stale-token-from-a-previous-process\n`,
    });
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 2);
    assert.equal(result.stdout, '');
    assert.match(result.stderr, /rejected the saved launch token/);
    assert.equal(existsSync(join(s.artifacts, 'argv.json')), false);
  });

  test('an off-origin redirect is refused', async (t) => {
    const s = await scenario('preflight-redirect', { web: { authMode: 'cross-origin' } });
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 2);
    assert.match(result.stderr, /redirected authentication off-origin/);
    assert.equal(existsSync(join(s.artifacts, 'argv.json')), false);
  });

  test('a token exchange without a session cookie is refused', async (t) => {
    const s = await scenario('preflight-no-cookie', { web: { authMode: 'no-cookie' } });
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 2);
    assert.match(result.stderr, /did not issue a session cookie/);
    assert.equal(existsSync(join(s.artifacts, 'argv.json')), false);
  });

  test('a hanging preflight is bounded by --web-timeout and starts no child', async (t) => {
    const s = await scenario('preflight-hang', { web: { authMode: 'hang' }, extraArgs: ['--web-timeout', '1'] });
    t.after(() => s.fixture.close());
    const started = Date.now();
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 2);
    assert.match(result.stderr, /timed out after 1s/);
    assert.ok(Date.now() - started < 10000, 'preflight timeout was bounded');
    assert.equal(existsSync(join(s.artifacts, 'argv.json')), false);
  });

  test('invalid URL shapes are rejected before any request', async () => {
    const dir = makeDir(root, 'bad-urls');
    const cwd = makeDir(dir, 'cwd');
    const artifacts = makeDir(dir, 'artifacts');
    const mock = writeMockDsh(makeDir(dir, 'bin'));
    const settingsFile = writeFile(dir, 'settings.yaml', '');
    const taskFile = writeFile(dir, 'task.md', 'Do it.\n');
    const cases = [
      ['http://192.0.2.10:3080/?token=x', /must point at localhost, 127\.0\.0\.1, or \[::1\]/],
      ['http://user:pass@127.0.0.1:3080/?token=x', /must not embed a username or password/],
      ['file:///tmp/web-url', /must be an http\(s\) URL/],
      ['http://127.0.0.1:3080/settings?token=x', /must point at the origin root/],
      ['not a url', /not a valid absolute URL/],
      ['http://127.0.0.1:3080/?token=a&token=b', /more than one token/],
    ];
    for (const [url, pattern] of cases) {
      const result = await runHttpCli([
        '--cwd', cwd, '--task-file', taskFile, '--settings-file', settingsFile,
        '--dsh-bin', mock, '--dsh-web-url', url,
      ], { env: testEnv({ MOCK_ARTIFACT_DIR: artifacts, XDG_CONFIG_HOME: makeDir(dir, 'xdg') }) });
      assert.equal(result.status, 2, `expected exit 2 for ${url}`);
      assert.equal(result.stdout, '');
      assert.match(result.stderr, pattern);
    }
    assert.equal(existsSync(join(artifacts, 'argv.json')), false);
  });
});

describe('grouping failures stay visible and nonzero', async () => {
  const cases = [
    ['session-create-error', { web: { sessionCreateMode: 'error' } }, /session\/not-found/],
    ['wrong-session-id', { web: { sessionCreateMode: 'wrong-id' } }, /different session id/],
    ['malformed-response', { web: { sessionCreateMode: 'malformed' } }, /malformed response/],
    ['membership-missing', { web: { attachMembership: false } }, /not listed in workspace membership/],
    ['capture-missing', { mockEnv: { MOCK_CAPTURE: 'off' } }, /did not write capture metadata/],
    ['capture-prompt-mismatch', { mockEnv: { MOCK_CAPTURE_PROMPT_SHA256: 'f'.repeat(64) } }, /does not match this run's prompt/],
    ['http-500', { web: { sessionCreateMode: 'http500' } }, /answered HTTP 500/],
  ];
  for (const [name, options, pattern] of cases) {
    test(`${name}: task outcome and log paths survive`, async (t) => {
      const s = await scenario(`failure-${name}`, options);
      t.after(() => s.fixture.close());
      const result = await runHttpCli(s.args, { env: s.env });

      assert.equal(result.status, 1, 'a failed grouping is visible as a nonzero exit');
      assertOneJsonLine(result.stdout);
      const payload = parsePayload(result);
      assert.equal(payload.status, 'ok', 'the original child status is preserved');
      assert.equal(payload.exitCode, 0);
      assert.equal(payload.finalText, 'mock dsh completed');
      assert.equal(existsSync(payload.logPaths.stdout), true);
      assert.equal(payload.workspace.enabled, true);
      assert.equal(payload.workspace.bound, false);
      assert.equal(payload.workspace.path, s.canonicalCwd);
      assert.match(payload.workspace.error, pattern);
    });
  }

  test('a spawn error reports no session to group without pretending to bind', async (t) => {
    const broken = writeFile(makeDir(root, 'failure-spawn'), 'broken-dsh', '#!/nonexistent/deepseek-delegate-interpreter\n');
    chmodSync(broken, 0o755);
    const s = await scenario('failure-spawn', { mock: broken });
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 1);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'spawn-error');
    assert.equal(payload.workspace.bound, false);
    assert.match(payload.workspace.error, /never started/);
    assert.equal(s.fixture.rpcRequests('session/create').length, 0);
  });

  test('a hanging adoption is bounded by --web-timeout and reports the captured session id', async (t) => {
    const s = await scenario('failure-adopt-hang', {
      web: { sessionCreateMode: 'hang' },
      extraArgs: ['--web-timeout', '1'],
    });
    t.after(() => s.fixture.close());
    const started = Date.now();
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 1);
    assert.ok(Date.now() - started < 15000, 'adoption timeout was bounded');
    const payload = parsePayload(result);
    assert.equal(payload.status, 'ok');
    assert.equal(payload.workspace.bound, false);
    assert.equal(payload.workspace.sessionId, SESSION_ID, 'the captured id stays available for a safe retry');
    assert.match(payload.workspace.error, /timed out after 1s/);
  });
});

describe('token and credential hygiene', async () => {
  test('the launch token never reaches output, patches, logs, or the child environment', async (t) => {
    const secret = 'tok_REDACT_ME_9137';
    const s = await scenario('redaction', {
      web: { token: secret, sessionCreateMode: 'error' },
      env: { DSH_WEB_URL: `http://127.0.0.1:1/?token=ENV_SECRET_5521` },
    });
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 1);
    const payload = parsePayload(result);
    assert.match(payload.workspace.error, /session\/not-found/);
    for (const [label, text] of [
      ['stdout', result.stdout],
      ['stderr', result.stderr],
      ['patch', readArtifact(s.artifacts, 'patch.json')],
      ['stdout log', readText(payload.logPaths.stdout)],
      ['stderr log', readText(payload.logPaths.stderr)],
      ['capture', payload.logPaths.capture === null ? '' : readText(payload.logPaths.capture)],
    ]) {
      assert.ok(!text.includes(secret), `${label} leaked the launch token`);
      assert.ok(!text.includes('ENV_SECRET_5521'), `${label} leaked the environment token`);
    }
    assert.equal(readArtifact(s.artifacts, 'web-env.txt'), 'unset', 'the token-bearing URL is not inherited by dsh');
  });
});

describe('--attach-session recovery mode', async () => {
  test('attaches an existing completed session without running a model task', async (t) => {
    const s = await scenario('attach-ok', { attach: 'session-existing-42' });
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 0, result.stderr);
    assertOneJsonLine(result.stdout);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'ok');
    assert.equal(payload.mode, 'attach');
    assert.equal(payload.requested, null);
    assert.equal(payload.logPaths, null);
    assert.equal(payload.workspace.bound, true);
    assert.equal(payload.workspace.sessionId, 'session-existing-42');
    assert.equal(payload.workspace.path, s.canonicalCwd);
    assert.deepEqual(s.fixture.rpcRequests('session/create')[0].payload, {
      sessionId: 'session-existing-42',
      workspaceId: 'workspace-1',
    });
    assert.equal(existsSync(join(s.artifacts, 'argv.json')), false, 'attach mode never spawns dsh');
  });

  test('an attach failure is JSON, nonzero, and never claims a binding', async (t) => {
    const s = await scenario('attach-failure', { attach: 'session-missing-7', web: { sessionCreateMode: 'error' } });
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 1);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'attach-error');
    assert.equal(payload.workspace.bound, false);
    assert.equal(payload.workspace.sessionId, 'session-missing-7');
    assert.match(payload.error, /session\/not-found/);
  });

  test('attach usage errors are rejected before any request', async () => {
    const dir = makeDir(root, 'attach-usage');
    const cwd = makeDir(dir, 'cwd');
    const taskFile = writeFile(dir, 'task.md', 'Do it.\n');
    const xdg = makeDir(dir, 'xdg');
    const cases = [
      [['--attach-session', 'session-x', '--task-file', taskFile, '--cwd', cwd], /does not take --task-file/],
      [['--attach-session', 'session-x', '--no-workspace', '--cwd', cwd], /requires workspace grouping/],
      [['--attach-session', '', '--cwd', cwd], /must not be blank/],
      [['--attach-session', 'bad id', '--cwd', cwd], /plain session id/],
      [['--attach-session', 'session-x'], /--cwd is required/],
    ];
    for (const [args, pattern] of cases) {
      const result = await runHttpCli(args, { env: testEnv({ XDG_CONFIG_HOME: xdg }) });
      assert.equal(result.status, 2, `expected exit 2 for ${args.join(' ')}`);
      assert.equal(result.stdout, '');
      assert.match(result.stderr, pattern);
    }
  });
});

describe('URL configuration precedence and offline opt-out', async () => {
  test('the default credential file is read when no explicit URL is given', async (t) => {
    const s = await scenario('default-file', { urlArgs: [] });
    t.after(() => s.fixture.close());
    const defaultDir = join(s.env.XDG_CONFIG_HOME, 'deepseek-delegate');
    mkdirSync(defaultDir, { recursive: true });
    writeFileSync(join(defaultDir, 'web-url'), `# saved by the user\n${s.fixture.launchUrl}\n`);
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 0, result.stderr);
    const payload = parsePayload(result);
    assert.equal(payload.workspace.bound, true);
    assert.equal(payload.workspace.sessionId, SESSION_ID);
  });

  test('a missing default file gives a clear setup error naming the path and the opt-out', async () => {
    const dir = makeDir(root, 'default-missing');
    const cwd = makeDir(dir, 'cwd');
    const artifacts = makeDir(dir, 'artifacts');
    const mock = writeMockDsh(makeDir(dir, 'bin'));
    const taskFile = writeFile(dir, 'task.md', 'Do it.\n');
    const xdg = makeDir(dir, 'xdg');
    const result = await runHttpCli(
      ['--cwd', cwd, '--task-file', taskFile, '--dsh-bin', mock],
      { env: testEnv({ MOCK_ARTIFACT_DIR: artifacts, XDG_CONFIG_HOME: xdg }) },
    );

    assert.equal(result.status, 2);
    assert.equal(result.stdout, '');
    assert.match(result.stderr, /deepseek-delegate[/\\]web-url/);
    assert.match(result.stderr, /--no-workspace/);
    assert.equal(existsSync(join(artifacts, 'argv.json')), false);
  });

  test('explicit --dsh-web-url beats --dsh-web-url-file, environment, and default', async (t) => {
    const s = await scenario('precedence', {
      urlArgs: ['--dsh-web-url', 'http://127.0.0.1:1/?token=explicit', '--dsh-web-url-file', join(root, 'does-not-exist')],
      env: { DSH_WEB_URL: 'http://127.0.0.1:2/?token=env' },
    });
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    // The explicit URL wins, so the CLI reaches 127.0.0.1:1 and fails there
    // without ever reading the file or the environment URL.
    assert.equal(result.status, 2);
    assert.match(result.stderr, /127\.0\.0\.1:1/);
    assert.equal(s.fixture.requests.length, 0);
    assert.ok(!result.stderr.includes('explicit'));
  });

  test('--no-workspace keeps standalone/offline behavior and ignores web configuration', async (t) => {
    const s = await scenario('offline', {
      urlArgs: ['--no-workspace'],
      env: { DSH_WEB_URL: 'http://127.0.0.1:1/?token=ignored' },
    });
    t.after(() => s.fixture.close());
    const result = await runHttpCli(s.args, { env: s.env });

    assert.equal(result.status, 0, result.stderr);
    const payload = parsePayload(result);
    assert.deepEqual(payload.workspace, {
      enabled: false,
      bound: false,
      id: null,
      path: s.canonicalCwd,
      sessionId: null,
    });
    assert.deepEqual(payload.requested, DEFAULT_ROUTE);
    assert.equal(s.fixture.requests.length, 0, 'no web host is contacted');
    const patch = readArtifactJson(s.artifacts, 'patch.json');
    assert.deepEqual(patch, [{ id: 'settings', config: { path: join(readArtifact(s.artifacts, 'settings-copy-dir.txt'), 'settings.json'), watch: false } }]);
    assert.equal(readArtifact(s.artifacts, 'web-env.txt'), 'unset', 'the token-bearing URL is not inherited by dsh either');
  });

  test('--no-workspace cannot be combined with explicit web URL flags', async () => {
    const result = await runHttpCli(['--help', '--no-workspace', '--dsh-web-url', 'http://127.0.0.1:3080/?token=x']);
    assert.equal(result.status, 0, '--help short-circuits before validation');
    const bad = await runHttpCli(['--cwd', root, '--task-file', join(root, 'missing.md'), '--no-workspace', '--dsh-web-url', 'http://127.0.0.1:3080/?token=x']);
    assert.equal(bad.status, 2);
    assert.match(bad.stderr, /cannot be combined/);
  });

  test('--web-timeout is validated as a bounded integer', async () => {
    const cases = [
      [['--web-timeout', '0'], /--web-timeout must be an integer/],
      [['--web-timeout', '121'], /--web-timeout must be an integer/],
      [['--web-timeout', '1.5'], /--web-timeout must be an integer/],
    ];
    for (const [args, pattern] of cases) {
      const result = await runHttpCli(['--cwd', root, '--task-file', join(root, 'missing.md'), ...args]);
      assert.equal(result.status, 2);
      assert.match(result.stderr, pattern);
    }
  });
});
