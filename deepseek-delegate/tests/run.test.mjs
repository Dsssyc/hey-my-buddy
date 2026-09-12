/**
 * Externally visible CLI behavior for deepseek-delegate.
 *
 * Every test runs `scripts/run.mjs` as a child process against a copy of the
 * mock dsh in a temporary workspace. No test imports the CLI internals, reads
 * real user settings, uses HOME/CODEX_HOME, or starts a paid model run.
 *
 * These are the original CLI behavior tests: every run passes the explicit
 * `--no-workspace` offline opt-out so no web host, token file, or network is
 * needed here. Workspace grouping has its own suite in `workspace.test.mjs`.
 */
import assert from 'node:assert/strict';
import { chmodSync, existsSync, readdirSync, realpathSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, sep } from 'node:path';
import { after, before, describe, test } from 'node:test';
import {
  isAlive, makeDir, makeWorkspace, modeOf, parsePayload, readArtifact, readArtifactJson,
  readText, runCli, sha256, startCli, testEnv, waitFor, waitForProcessExit, writeFile, writeMockDsh,
} from './support/helpers.mjs';

const DEFAULT_ROUTE = { provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'max' };

let root;
before(() => {
  root = makeWorkspace();
});
after(() => {
  rmSync(root, { recursive: true, force: true });
});

/** One isolated workspace: cwd, artifacts dir, mock dsh, settings, task file. */
function scenario(name, { task = 'Do the bounded thing.\n', settings = '' } = {}) {
  const dir = makeDir(root, name);
  const cwd = makeDir(dir, 'cwd');
  const artifacts = makeDir(dir, 'artifacts');
  const mock = writeMockDsh(makeDir(dir, 'bin'));
  const settingsFile = writeFile(dir, 'settings.yaml', settings);
  const taskFile = writeFile(dir, 'task.md', task);
  const baseArgs = ['--cwd', cwd, '--task-file', taskFile, '--settings-file', settingsFile, '--no-workspace'];
  return {
    dir,
    cwd,
    artifacts,
    mock,
    settings: settingsFile,
    taskFile,
    baseArgs,
    args: [...baseArgs, '--dsh-bin', mock],
    env: testEnv({ MOCK_ARTIFACT_DIR: artifacts }),
  };
}

function countSettingsTempDirs(directory) {
  return readdirSync(directory).filter((name) => name.startsWith('deepseek-delegate-settings-')).length;
}

function assertSingleJsonLine(stdout) {
  assert.equal(stdout.trim().split('\n').length, 1, `stdout was not one line: ${stdout}`);
}

describe('help and usage errors', () => {
  test('--help needs no dsh, no settings file, and no credentials', () => {
    const dir = makeDir(root, 'help');
    const env = testEnv({ PATH: makeDir(dir, 'empty-path'), DSH_HOME: makeDir(dir, 'dsh-home') });
    const result = runCli(['--help', '--settings-file', join(dir, 'missing.yaml')], { env });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /Usage: node scripts\/run\.mjs/);
    assert.match(result.stdout, /--dsh-bin/);
    assert.equal(result.stderr, '');
  });

  test('missing required flags and out-of-range timeouts exit 2 on stderr', () => {
    const s = scenario('usage-errors');
    const cases = [
      [[], /--cwd is required/],
      [['--cwd', s.cwd], /--task-file is required/],
      [['--cwd', s.cwd, '--task-file', s.taskFile, '--timeout', '9'], /--timeout must be an integer/],
      [['--cwd', s.cwd, '--task-file', s.taskFile, '--timeout', '86401'], /--timeout must be an integer/],
      [['--cwd', s.cwd, '--task-file', s.taskFile, '--timeout', '1.5'], /--timeout must be an integer/],
      [['--cwd', join(s.dir, 'missing'), '--task-file', s.taskFile], /--cwd is not a directory/],
      [['--cwd', s.cwd, '--task-file', join(s.dir, 'missing.md')], /--task-file is not a file/],
    ];
    for (const [args, pattern] of cases) {
      const result = runCli(args, { env: s.env });
      assert.equal(result.status, 2, `expected exit 2 for ${args.join(' ')}`);
      assert.match(result.stderr, pattern);
      assert.equal(result.stdout, '');
    }
  });

  test('blank model/provider/effort values are rejected instead of clearing defaults', () => {
    const s = scenario('blank-route');
    const cases = [
      [['--model', ''], {}, /--model must not be blank/],
      [['--provider', '  '], {}, /--provider must not be blank/],
      [['--effort', ''], {}, /--effort must not be blank/],
      [[], { DSH_DELEGATE_MODEL: '' }, /DSH_DELEGATE_MODEL must not be blank/],
      [[], { DSH_DELEGATE_EFFORT: '   ' }, /DSH_DELEGATE_EFFORT must not be blank/],
    ];
    for (const [args, extraEnv, pattern] of cases) {
      const result = runCli([...s.args, ...args], { env: testEnv({ MOCK_ARTIFACT_DIR: s.artifacts, ...extraEnv }) });
      assert.equal(result.status, 2);
      assert.match(result.stderr, pattern);
      assert.equal(result.stdout, '');
    }
  });
});

describe('dsh launcher resolution', () => {
  test('finds dsh on PATH and uses the tested headless invocation', () => {
    const s = scenario('path-lookup');
    const env = testEnv({
      PATH: [dirname(s.mock), dirname(process.execPath), '/usr/bin', '/bin'].join(':'),
      MOCK_ARTIFACT_DIR: s.artifacts,
    });
    const result = runCli(s.baseArgs, { env });
    assert.equal(result.status, 0, result.stderr);
    assertSingleJsonLine(result.stdout);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'ok');
    assert.equal(payload.exitCode, 0);
    assert.equal(payload.signal, null);
    assert.equal(payload.error, null);
    assert.deepEqual(payload.requested, DEFAULT_ROUTE);
    assert.equal(payload.inputDelivery, 'inline');
    assert.equal(payload.finalText, 'mock dsh completed');
    assert.equal(payload.finalTextTruncated, false);
    assert.equal(payload.taskFile, s.taskFile);

    const argv = readArtifactJson(s.artifacts, 'argv.json');
    assert.equal(argv.length, 7);
    assert.deepEqual(argv.slice(0, 3), ['--profile', 'headless', '--patch']);
    assert.equal(argv[4], '--');
    assert.equal(argv[5], '--');
    assert.equal(argv[6], 'Do the bounded thing.\n');

    const patch = readArtifactJson(s.artifacts, 'patch.json');
    const copyDir = readArtifact(s.artifacts, 'settings-copy-dir.txt');
    assert.deepEqual(patch, [{ id: 'settings', config: { path: join(copyDir, 'settings.json'), watch: false } }]);
    assert.ok(basename(copyDir).startsWith('deepseek-delegate-settings-'));
    assert.deepEqual(readArtifactJson(s.artifacts, 'settings-copy.json'), { 'agent-default-model': DEFAULT_ROUTE });

    // The private settings copy is gone once the owned process has finished.
    assert.equal(existsSync(copyDir), false);
  });

  test('--dsh-bin beats DSH_BIN and resolves relative to the invoking cwd', () => {
    const dir = makeDir(root, 'launcher-precedence');
    const cwd = makeDir(dir, 'run-here');
    const artifacts = makeDir(dir, 'artifacts');
    const taskFile = writeFile(dir, 'task.md', 'Do it.\n');
    const settingsFile = writeFile(dir, 'settings.yaml', '');
    const mockA = writeMockDsh(join(dir, 'mock a'), 'dsh');
    const mockB = writeMockDsh(join(dir, 'mock b'), 'dsh');
    const env = testEnv({ MOCK_ARTIFACT_DIR: artifacts, DSH_BIN: mockB });

    // Relative --dsh-bin and a cwd that differs from --cwd.
    const result = runCli(
      ['--cwd', cwd, '--task-file', taskFile, '--settings-file', settingsFile, '--no-workspace', '--dsh-bin', './mock a/dsh'],
      { cwd: dir, env },
    );
    assert.equal(result.status, 0, result.stderr);
    assert.equal(readArtifact(artifacts, 'self.txt'), realpathSync(mockA));

    // DSH_BIN is honored when the flag is absent.
    const artifacts2 = makeDir(dir, 'artifacts-dsh-bin');
    const result2 = runCli(['--cwd', cwd, '--task-file', taskFile, '--settings-file', settingsFile, '--no-workspace'], {
      cwd: dir,
      env: testEnv({ MOCK_ARTIFACT_DIR: artifacts2, DSH_BIN: mockB }),
    });
    assert.equal(result2.status, 0, result2.stderr);
    assert.equal(readArtifact(artifacts2, 'self.txt'), realpathSync(mockB));
  });

  test('rejects a dsh launcher that is missing, non-executable, or blank', () => {
    const s = scenario('bad-launcher');
    const plain = writeFile(s.dir, 'plain.txt', 'not executable\n');
    const cases = [
      [['--dsh-bin', join(s.dir, 'no-such-dsh')], {}, /not an executable file/],
      [['--dsh-bin', plain], {}, /not an executable file/],
      [['--dsh-bin', ''], {}, /must not be blank/],
      [[], { DSH_BIN: '   ' }, /must not be blank/],
    ];
    for (const [args, extraEnv, pattern] of cases) {
      const result = runCli([...s.baseArgs, ...args], { env: testEnv({ ...extraEnv }) });
      assert.equal(result.status, 2);
      assert.match(result.stderr, pattern);
      assert.equal(result.stdout, '');
    }
  });

  test('a launcher that cannot execute reports spawn-error as JSON and cleans up', () => {
    const s = scenario('spawn-error');
    const broken = writeFile(s.dir, 'broken-dsh', '#!/nonexistent/deepseek-delegate-interpreter\n');
    chmodSync(broken, 0o755);
    const privateTmp = makeDir(s.dir, 'private-tmp');
    const before = countSettingsTempDirs(privateTmp);
    const result = runCli(
      ['--cwd', s.cwd, '--task-file', s.taskFile, '--settings-file', s.settings, '--no-workspace', '--dsh-bin', broken],
      { env: { ...s.env, TMPDIR: privateTmp } },
    );
    assert.equal(result.status, 1);
    assertSingleJsonLine(result.stdout);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'spawn-error');
    assert.ok(payload.error, 'spawn-error carries a reason');
    assert.equal(countSettingsTempDirs(privateTmp), before, 'settings copies are cleaned up after spawn failure');
  });
});

describe('task delivery', () => {
  test('leading dashes, newlines, and shell metacharacters arrive literally', () => {
    const task = '--model evil\n$(touch PWNED_CMD) `touch PWNED_TICK` ; echo "x" | tee PWNED_PIPE\nrm -rf /\n';
    const s = scenario('literal-task', { task });
    const result = runCli(s.args, { env: s.env });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(readArtifactJson(s.artifacts, 'argv.json')[6], task);
    assert.equal(readText(s.taskFile), task);
    for (const name of ['PWNED_CMD', 'PWNED_TICK', 'PWNED_PIPE']) {
      assert.equal(existsSync(join(s.cwd, name)), false, `${name} must not be created in --cwd`);
      assert.equal(existsSync(join(s.dir, name)), false, `${name} must not be created next to the task file`);
    }
  });

  test('task content above 32000 bytes travels as a file reference', () => {
    const s = scenario('file-reference');
    const inlineTask = writeFile(s.dir, 'inline.md', 'A'.repeat(32000));
    const largeTask = writeFile(s.dir, 'large.md', 'B'.repeat(32001));

    const small = runCli(
      ['--cwd', s.cwd, '--task-file', inlineTask, '--settings-file', s.settings, '--no-workspace', '--dsh-bin', s.mock],
      { env: s.env },
    );
    assert.equal(small.status, 0, small.stderr);
    assert.equal(parsePayload(small).inputDelivery, 'inline');
    assert.equal(readArtifactJson(s.artifacts, 'argv.json')[6], 'A'.repeat(32000));

    const artifacts2 = makeDir(s.dir, 'artifacts-large');
    const large = runCli(
      ['--cwd', s.cwd, '--task-file', largeTask, '--settings-file', s.settings, '--no-workspace', '--dsh-bin', s.mock],
      { env: testEnv({ MOCK_ARTIFACT_DIR: artifacts2 }) },
    );
    assert.equal(large.status, 0, large.stderr);
    const payload = parsePayload(large);
    assert.equal(payload.inputDelivery, 'file-reference');
    assert.equal(payload.taskFile, largeTask);
    const prompt = readArtifactJson(artifacts2, 'argv.json')[6];
    assert.ok(prompt.includes(largeTask), 'bootstrap names the task file');
    assert.ok(!prompt.includes('BBBB'), 'bootstrap does not inline the task body');
  });

  test('large dsh stdout is summarized to the 6000 byte cap and stays one JSON line', () => {
    const s = scenario('big-stdout');
    const result = runCli(s.args, {
      env: testEnv({ MOCK_ARTIFACT_DIR: s.artifacts, MOCK_MODE: 'big', MOCK_BYTES: '200000' }),
    });
    assert.equal(result.status, 0, result.stderr);
    assertSingleJsonLine(result.stdout);
    const payload = parsePayload(result);
    assert.equal(Buffer.byteLength(payload.finalText), 6000);
    assert.equal(payload.finalTextTruncated, true);
    assert.equal(statSync(payload.logPaths.stdout).size, 200000);
  });

  test('the stdout cap never corrupts a partial UTF-8 character', () => {
    const s = scenario('unicode-stdout');
    const prefix = '界'.repeat(1999);
    const result = runCli(s.args, {
      env: testEnv({ MOCK_ARTIFACT_DIR: s.artifacts, MOCK_STDOUT: prefix + '😀tail' }),
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(parsePayload(result).finalText, prefix);
    assert.equal(parsePayload(result).finalTextTruncated, true);
  });

  test('every run gets unique owner-private logs and never reuses existing files', () => {
    const s = scenario('unique-logs');
    const logsParent = makeDir(s.dir, 'logs');
    const sentinel = writeFile(logsParent, 'stdout.log', 'keep me\n');
    const first = runCli([...s.args, '--log-dir', logsParent], { env: s.env });
    const second = runCli([...s.args, '--log-dir', logsParent], { env: s.env });
    assert.equal(first.status, 0, first.stderr);
    assert.equal(second.status, 0, second.stderr);

    const one = parsePayload(first);
    const two = parsePayload(second);
    assert.notEqual(one.logPaths.stdout, two.logPaths.stdout);
    for (const payload of [one, two]) {
      const logDir = dirname(payload.logPaths.stdout);
      assert.ok(logDir.startsWith(logsParent + sep), 'log dir lives under --log-dir');
      assert.ok(basename(logDir).startsWith('deepseek-delegate-run-'));
      assert.equal(modeOf(logDir), '700');
      assert.equal(modeOf(payload.logPaths.stdout), '600');
      assert.equal(modeOf(payload.logPaths.stderr), '600');
      assert.equal(readText(payload.logPaths.stdout), 'mock dsh completed\n');
    }
    assert.equal(readText(sentinel), 'keep me\n', 'pre-existing files are untouched');
  });
});

describe('settings handling', () => {
  test('explicit settings files must exist; a missing default is an empty mapping', () => {
    const s = scenario('settings-missing');
    const missing = join(s.dir, 'nope.yaml');
    const argsWithoutSettings = ['--cwd', s.cwd, '--task-file', s.taskFile, '--dsh-bin', s.mock, '--no-workspace'];

    let result = runCli([...argsWithoutSettings, '--settings-file', missing], { env: s.env });
    assert.equal(result.status, 2);
    assert.match(result.stderr, /settings file does not exist/);
    assert.equal(result.stdout, '');

    result = runCli(argsWithoutSettings, {
      env: testEnv({ MOCK_ARTIFACT_DIR: s.artifacts, DSH_SETTINGS_FILE: missing }),
    });
    assert.equal(result.status, 2);
    assert.match(result.stderr, /settings file does not exist/);

    const artifacts2 = makeDir(s.dir, 'artifacts-default');
    const dshHome = makeDir(s.dir, 'dsh-home');
    result = runCli(argsWithoutSettings, {
      env: testEnv({ MOCK_ARTIFACT_DIR: artifacts2, DSH_HOME: dshHome }),
    });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(readArtifactJson(artifacts2, 'settings-copy.json'), { 'agent-default-model': DEFAULT_ROUTE });
  });

  test('--settings-file beats DSH_SETTINGS_FILE beats $DSH_HOME, other sections survive', () => {
    const dir = makeDir(root, 'settings-precedence');
    const cwd = makeDir(dir, 'cwd');
    const artifacts = makeDir(dir, 'artifacts');
    const mock = writeMockDsh(makeDir(dir, 'bin'));
    const taskFile = writeFile(dir, 'task.md', 'Do it.\n');
    const home = makeDir(dir, 'dsh-home');
    const homeSettings = writeFile(home, 'settings.yaml', 'locale: home-locale\n');
    const envSettings = writeFile(dir, 'env.yaml', 'locale: env-locale\n');
    const flagSettings = writeFile(dir, 'flag.yaml', [
      'locale: flag-locale',
      'agent-default-model:',
      '  provider: original-provider',
      '  model: original-model',
      '  reasoningEffort: low',
      '  contextWindow: 1000000',
      'agent-presets:',
      '  - name: one',
      '    settings:',
      '      temperature: 0.2',
      'permission:',
      '  mode: ask',
      '',
    ].join('\n'));
    const args = ['--cwd', cwd, '--task-file', taskFile, '--dsh-bin', mock, '--settings-file', flagSettings, '--no-workspace'];
    const hashes = [sha256(homeSettings), sha256(envSettings), sha256(flagSettings)];

    const result = runCli(args, {
      env: testEnv({ MOCK_ARTIFACT_DIR: artifacts, DSH_HOME: home, DSH_SETTINGS_FILE: envSettings }),
    });
    assert.equal(result.status, 0, result.stderr);
    const payload = parsePayload(result);
    // The original selection keeps its provider/model, effort is never silently
    // inherited, and unrelated keys in the section (context window) survive.
    assert.deepEqual(payload.requested, {
      provider: 'original-provider',
      model: 'original-model',
      reasoningEffort: 'max',
    });
    const copy = readArtifactJson(artifacts, 'settings-copy.json');
    assert.equal(copy.locale, 'flag-locale');
    assert.deepEqual(copy['agent-presets'], [{ name: 'one', settings: { temperature: 0.2 } }]);
    assert.deepEqual(copy.permission, { mode: 'ask' });
    assert.deepEqual(copy['agent-default-model'], {
      provider: 'original-provider',
      model: 'original-model',
      reasoningEffort: 'max',
      contextWindow: 1000000,
    });
    assert.deepEqual([sha256(homeSettings), sha256(envSettings), sha256(flagSettings)], hashes);

    // Environment file wins once the flag is absent.
    const artifacts2 = makeDir(dir, 'artifacts-env');
    const result2 = runCli(['--cwd', cwd, '--task-file', taskFile, '--dsh-bin', mock, '--no-workspace'], {
      env: testEnv({ MOCK_ARTIFACT_DIR: artifacts2, DSH_HOME: home, DSH_SETTINGS_FILE: envSettings }),
    });
    assert.equal(result2.status, 0, result2.stderr);
    assert.equal(readArtifactJson(artifacts2, 'settings-copy.json').locale, 'env-locale');

    // $DSH_HOME/settings.yaml is the last default.
    const artifacts3 = makeDir(dir, 'artifacts-home');
    const result3 = runCli(['--cwd', cwd, '--task-file', taskFile, '--dsh-bin', mock, '--no-workspace'], {
      env: testEnv({ MOCK_ARTIFACT_DIR: artifacts3, DSH_HOME: home }),
    });
    assert.equal(result3.status, 0, result3.stderr);
    assert.equal(readArtifactJson(artifacts3, 'settings-copy.json').locale, 'home-locale');
  });

  test('route precedence: CLI flags beat environment, environment beats settings', () => {
    const s = scenario('route-precedence', {
      settings: 'agent-default-model:\n  provider: settings-provider\n  model: settings-model\n  reasoningEffort: low\n',
    });
    const artifactsEnv = makeDir(s.dir, 'artifacts-env');
    const environment = {
      MOCK_ARTIFACT_DIR: artifactsEnv,
      DSH_DELEGATE_PROVIDER: 'env-provider',
      DSH_DELEGATE_MODEL: 'env-model',
      DSH_DELEGATE_EFFORT: 'medium',
    };
    let result = runCli(s.args, { env: testEnv(environment) });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(parsePayload(result).requested, {
      provider: 'env-provider',
      model: 'env-model',
      reasoningEffort: 'medium',
    });

    const artifactsCli = makeDir(s.dir, 'artifacts-cli');
    result = runCli([...s.args, '--model', 'cli-model', '--provider', 'cli-provider', '--effort', 'high'], {
      env: testEnv({ ...environment, MOCK_ARTIFACT_DIR: artifactsCli }),
    });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(parsePayload(result).requested, {
      provider: 'cli-provider',
      model: 'cli-model',
      reasoningEffort: 'high',
    });
  });

  test('unrelated selection settings do not select or invalidate the model route', () => {
    for (const [name, section] of [['mapping', { provider: 'unrelated-provider' }], ['scalar', 'editor-selection']]) {
      const s = scenario(`unrelated-selection-${name}`, { settings: JSON.stringify({ selection: section }) });
      const result = runCli(s.args, { env: s.env });
      assert.equal(result.status, 0, result.stderr);
      assert.deepEqual(parsePayload(result).requested, DEFAULT_ROUTE);
      assert.deepEqual(readArtifactJson(s.artifacts, 'settings-copy.json').selection, section);
    }
  });

  test('relative DSH_HOME still identifies the same directory after changing cwd', () => {
    const s = scenario('relative-dsh-home');
    const configHome = makeDir(s.dir, 'dsh-home');
    writeFile(configHome, 'settings.yaml', 'agent-default-model:\n  model: configured-model\n');
    const result = runCli(['--cwd', s.cwd, '--task-file', s.taskFile, '--dsh-bin', s.mock, '--no-workspace'], {
      cwd: s.dir,
      env: testEnv({ MOCK_ARTIFACT_DIR: s.artifacts, DSH_HOME: 'dsh-home' }),
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(parsePayload(result).requested.model, 'configured-model');
    assert.equal(realpathSync(readArtifact(s.artifacts, 'dsh-home.txt')), realpathSync(configHome));
  });

  test('accepts JSON settings documents', () => {
    const s = scenario('json-settings');
    const jsonSettings = writeFile(s.dir, 'settings.json', JSON.stringify({
      locale: 'en',
      'agent-default-model': { provider: 'json-provider', model: 'json-model' },
    }));
    const artifacts = makeDir(s.dir, 'artifacts-json');
    const result = runCli(
      ['--cwd', s.cwd, '--task-file', s.taskFile, '--dsh-bin', s.mock, '--settings-file', jsonSettings, '--no-workspace'],
      { env: testEnv({ MOCK_ARTIFACT_DIR: artifacts }) },
    );
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(parsePayload(result).requested, {
      provider: 'json-provider',
      model: 'json-model',
      reasoningEffort: 'max',
    });
    assert.equal(readArtifactJson(artifacts, 'settings-copy.json').locale, 'en');
  });

  test('mapping and type errors fail clearly without echoing settings contents', () => {
    const s = scenario('settings-errors');
    const secret = 'SECRET-TOKEN-9137';
    const cases = [
      [`- ${secret}\n`, /top-level mapping/],
      [`agent-default-model: not-a-mapping\napi-key: ${secret}\n`, /"agent-default-model" must be a mapping/],
      [`locale: [unclosed\napi-key: ${secret}\n`, /not valid YAML or JSON/],
      [`agent-default-model:\n  provider: p\n  model: 42\n`, /must be a non-empty string/],
    ];
    for (const [index, [content, pattern]] of cases.entries()) {
      const bad = writeFile(s.dir, `bad-${index}.yaml`, content);
      const result = runCli([...s.baseArgs, '--dsh-bin', s.mock, '--settings-file', bad], { env: s.env });
      assert.equal(result.status, 2, `case ${index} should exit 2`);
      assert.match(result.stderr, pattern);
      assert.ok(!result.stderr.includes(secret), `case ${index} leaked settings content`);
      assert.equal(result.stdout, '');
    }
  });
});

describe('outcomes and process lifecycle', () => {
  test('a nonzero dsh exit is reported as nonzero and stderr stays in the log', () => {
    const s = scenario('nonzero');
    const result = runCli(s.args, {
      env: testEnv({ MOCK_ARTIFACT_DIR: s.artifacts, MOCK_MODE: 'nonzero', MOCK_EXIT_CODE: '3', MOCK_STDERR: 'boom-secret\n' }),
    });
    assert.equal(result.status, 1);
    assertSingleJsonLine(result.stdout);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'nonzero');
    assert.equal(payload.exitCode, 3);
    assert.equal(payload.error, null);
    assert.ok(!result.stdout.includes('boom-secret'), 'stderr is not copied into the JSON');
    assert.equal(readText(payload.logPaths.stderr), 'boom-secret\n');
  });

  test('timeout stops the owned child group and never reports ok', { timeout: 60000 }, async () => {
    const s = scenario('timeout');
    const result = runCli([...s.args, '--timeout', '10'], {
      env: testEnv({ MOCK_ARTIFACT_DIR: s.artifacts, MOCK_MODE: 'hang' }),
      timeoutMs: 30000,
    });
    assert.equal(result.status, 1);
    assertSingleJsonLine(result.stdout);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'timeout');
    assert.equal(payload.timeoutSeconds, 10);
    assert.ok(payload.elapsedSeconds >= 10, `elapsed ${payload.elapsedSeconds}s`);
    assert.match(payload.error, /timeout/);
    const pids = readArtifactJson(s.artifacts, 'pids.json');
    await waitForProcessExit(pids.self, 5000);
    assert.equal(existsSync(readArtifact(s.artifacts, 'settings-copy-dir.txt')), false);
  });

  test('cancellation is reported even when the child exits 0 afterwards', { timeout: 30000 }, async () => {
    const s = scenario('cancel-exit-zero');
    const { child, done } = startCli(s.args, { env: testEnv({ MOCK_ARTIFACT_DIR: s.artifacts, MOCK_MODE: 'exit0-on-term' }) });
    await waitFor(() => existsSync(join(s.artifacts, 'pids.json')), 'mock dsh to record its pids');
    child.kill('SIGTERM');
    const result = await done;

    assert.equal(result.code, 1);
    assertSingleJsonLine(result.stdout);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'cancelled');
    assert.match(payload.error, /cancelled by SIGTERM/);
    const pids = readArtifactJson(s.artifacts, 'pids.json');
    assert.ok(pids.grandchild !== null);
    await waitForProcessExit(pids.self, 5000);
    await waitForProcessExit(pids.grandchild, 5000);
    assert.equal(existsSync(readArtifact(s.artifacts, 'settings-copy-dir.txt')), false);
  });

  test('a child that ignores SIGTERM is escalated to SIGKILL within the grace period', { timeout: 30000 }, async () => {
    const s = scenario('cancel-ignore');
    const started = Date.now();
    const { child, done } = startCli(s.args, { env: testEnv({ MOCK_ARTIFACT_DIR: s.artifacts, MOCK_MODE: 'ignore' }) });
    await waitFor(() => existsSync(join(s.artifacts, 'pids.json')), 'mock dsh to record its pids');
    child.kill('SIGTERM');
    const result = await done;

    assert.equal(result.code, 1);
    const payload = parsePayload(result);
    assert.equal(payload.status, 'cancelled');
    assert.ok(Date.now() - started >= 3000, 'SIGKILL escalation waited for the grace period');
    const pids = readArtifactJson(s.artifacts, 'pids.json');
    assert.ok(pids.grandchild !== null);
    await waitForProcessExit(pids.self, 5000);
    await waitForProcessExit(pids.grandchild, 5000);
    assert.equal(isAlive(pids.self), false);
    assert.equal(existsSync(readArtifact(s.artifacts, 'settings-copy-dir.txt')), false);
  });
});
