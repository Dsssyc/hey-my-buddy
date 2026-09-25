/**
 * Externally visible behavior of the lightweight decision adapter.
 *
 * Every test runs `scripts/decision.mjs` as a child process against a synthetic
 * dsh launcher. No test boots the real harness, reads real settings or
 * credentials, or makes a paid model call: the stub records the argv and loader
 * patch the adapter composed and returns a canned child outcome, so process
 * handling, envelope validation, timeout cleanup, and error reporting are all
 * exercised for real.
 */
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { after, before, describe, test } from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS, MIN_TIMEOUT_SECONDS,
  composePatch, isEntrypoint, parseArgs, resolveDshBin, validateRequest,
} from '../scripts/decision.mjs';
import { derivePolicyFacts } from '../scripts/selection-policy.mjs';

const CLI_PATH = fileURLToPath(new URL('../scripts/decision.mjs', import.meta.url));
const PLUGIN_PATH = fileURLToPath(new URL('../plugins/decision.mjs', import.meta.url));
const STUB_FIXTURE = fileURLToPath(new URL('./support/mock-decision-dsh.mjs', import.meta.url));

let root;
before(() => {
  root = mkdtempSync(join(tmpdir(), 'decision-adapter-test-'));
});
after(() => {
  rmSync(root, { recursive: true, force: true });
});

/** A clean environment: the real dsh home, settings and credentials cannot leak in. */
function baseEnv(overrides = {}) {
  const env = { ...process.env };
  for (const key of [
    'DSH_HOME', 'DSH_BIN', 'DSH_SETTINGS_FILE', 'DSH_DELEGATE_MODEL', 'DSH_DELEGATE_PROVIDER', 'DSH_DELEGATE_EFFORT',
    'NODE_OPTIONS', 'NODE_USE_ENV_PROXY',
  ]) {
    delete env[key];
  }
  for (const key of Object.keys(env)) {
    if (key.startsWith('MOCK_DECISION_')) delete env[key];
  }
  return Object.assign(env, overrides);
}

let caseCounter = 0;
/**
 * One isolated scenario directory with a stub dsh on PATH.
 *
 * A private DSH_HOME with an installed `headless` profile is created for every
 * scenario: the adapter refuses to run against a harness home where the
 * profile would have to be created, and that refusal has its own test.
 */
function scenario({ request, scenarioName = 'ok', answer, code, timeout, settingsFile, argv = [], dump, installProfile = true } = {}) {
  caseCounter += 1;
  const dir = join(root, `case-${String(caseCounter)}`);
  const bin = join(dir, 'bin');
  const records = join(dir, 'records');
  const artifacts = join(dir, 'artifacts');
  const dshHome = join(dir, 'dsh-home');
  mkdirSync(bin, { recursive: true });
  mkdirSync(records, { recursive: true });
  mkdirSync(artifacts, { recursive: true });
  if (installProfile) {
    const profileDir = join(dshHome, 'profiles', 'headless');
    mkdirSync(profileDir, { recursive: true });
    writeFileSync(join(profileDir, 'package.json'), JSON.stringify({ name: 'dsh-profile-headless' }));
  } else {
    mkdirSync(dshHome, { recursive: true });
  }
  const dsh = join(bin, 'dsh');
  writeFileSync(dsh, readFileSync(STUB_FIXTURE), { mode: 0o755 });
  chmodSync(dsh, 0o755);
  const inputFile = join(dir, 'request.json');
  const outputFile = join(dir, 'output', 'result.json');
  const requestValue = request === undefined ? defaultRequest() : request;
  if (requestValue !== null) writeFileSync(inputFile, typeof requestValue === 'string' ? requestValue : JSON.stringify(requestValue, null, 2));
  const env = baseEnv({
    PATH: `${bin}:${dirname(process.execPath)}:/usr/bin:/bin`,
    DSH_HOME: dshHome,
    MOCK_DECISION_DIR: records,
    MOCK_DECISION_SCENARIO: scenarioName,
    ...(answer === undefined ? {} : { MOCK_DECISION_ANSWER: answer }),
    ...(code === undefined ? {} : { MOCK_DECISION_CODE: code }),
    ...(dump === undefined ? {} : { MOCK_DECISION_DUMP: dump }),
    ...(settingsFile === undefined ? {} : { DSH_SETTINGS_FILE: settingsFile }),
  });
  const args = ['--input-file', inputFile, '--output-file', outputFile, ...(timeout === undefined ? [] : ['--timeout', String(timeout)]), ...argv];
  return { dir, dshHome, bin, records, artifacts, dsh, inputFile, outputFile, env, args };
}

/** The canonical valid `select` request used by most scenarios. */
function defaultRequest(overrides = {}) {
  const profiles = [
    { profileId: 'p1', provider: 'deepseek-official', model: 'deepseek-flash', effort: 'off', available: true, enabled: true, evidenceIds: ['e1'] },
    { profileId: 'p2', provider: 'deepseek-official', model: 'deepseek-pro', effort: 'high', available: false, enabled: true, evidenceIds: [] },
    { profileId: 'p3', provider: 'deepseek-official', model: 'deepseek-flash', effort: 'off', available: true, enabled: false, evidenceIds: [] },
  ];
  const cards = [];
  const preferences = [];
  const routingPreferences = [];
  const annotations = [];
  const base = {
    operation: 'select',
    requestId: 'req-1',
    profile: { provider: 'deepseek-official', model: 'deepseek-flash', effort: 'off' },
    tableRevision: 7,
    task: 'Choose a model profile for the bounded task.',
    profiles,
    cards,
    preferences,
    evidence: [{ evidenceId: 'e2', profileId: 'p1', kind: 'human', summary: 'ok', source: 'test' }],
    annotations,
    routingPreferences,
  };
  return {
    ...base,
    policyFacts: derivePolicyFacts({ profiles, preferences, routingPreferences, hardConstraints: {} }),
    ...overrides,
  };
}

/** Run the CLI to completion. */
function runDecisionCli(s, { timeoutMs = 60_000, env } = {}) {
  return spawnSync(process.execPath, [CLI_PATH, ...s.args], { env: env ?? s.env, encoding: 'utf8', timeout: timeoutMs });
}

/** Read the envelope the CLI wrote, or fail loudly. */
function readEnvelope(s) {
  assert.ok(existsSync(s.outputFile), `no envelope was written: ${s.outputFile}`);
  return JSON.parse(readFileSync(s.outputFile, 'utf8'));
}

/** Read one stub recording file. */
function readRecord(s, name) {
  const file = join(s.records, name);
  assert.ok(existsSync(file), `the stub did not record ${name}`);
  return JSON.parse(readFileSync(file, 'utf8'));
}

/** Read one plain-text stub recording. */
function readRecordText(s, name) {
  const file = join(s.records, name);
  assert.ok(existsSync(file), `the stub did not record ${name}`);
  return readFileSync(file, 'utf8');
}

/** Split string args the way `parseArgs` expects them. */
function cliArgs(argv) {
  return argv.flatMap((token) => token.split(' ')).filter((token) => token !== '');
}

describe('usage and help', () => {
  test('--help needs no dsh, no input file and no credentials', () => {
    const result = spawnSync(process.execPath, [CLI_PATH, '--help'], { env: baseEnv(), encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /Usage: node scripts\/decision\.mjs/);
    assert.match(result.stdout, /--input-file/);
    assert.equal(result.stderr, '');
  });

  test('missing required flags and bad timeouts exit 2 with no output file', () => {
    const dir = join(root, 'usage');
    mkdirSync(dir, { recursive: true });
    const cases = [
      [[], /--input-file is required/],
      [['--input-file', join(dir, 'x.json')], /--output-file is required/],
      [['--input-file', join(dir, 'x.json'), '--output-file', join(dir, 'y.json'), '--timeout', '4'], /--timeout must be an integer from/],
      [['--input-file', join(dir, 'x.json'), '--output-file', join(dir, 'y.json'), '--timeout', String(MAX_TIMEOUT_SECONDS + 1)], /--timeout must be an integer from/],
      [['--input-file', join(dir, 'x.json'), '--output-file', join(dir, 'y.json'), '--timeout', 'soon'], /--timeout must be an integer number of seconds/],
      [['--input-file', join(dir, 'x.json'), '--output-file', join(dir, 'y.json'), '--nope', '1'], /unknown option: --nope/],
      [['positional'], /unexpected argument: positional/],
      [['--input-file'], /--input-file requires a value/],
      [['--input-file', 'a', '--input-file', 'b', '--output-file', 'c'], /more than once/],
    ];
    for (const [argv, pattern] of cases) {
      const result = spawnSync(process.execPath, [CLI_PATH, ...argv], { env: baseEnv(), encoding: 'utf8' });
      assert.equal(result.status, 2, `${argv.join(' ')} => ${result.stdout}${result.stderr}`);
      assert.match(result.stderr, pattern);
    }
    assert.equal(existsSync(join(dir, 'y.json')), false, 'a usage error must not write an envelope');
  });

  test('parseArgs returns the documented defaults', () => {
    const parsed = parseArgs(['--input-file', 'in.json', '--output-file', 'out.json']);
    assert.equal(parsed.timeoutSeconds, DEFAULT_TIMEOUT_SECONDS);
    assert.equal(parsed.dshBin, undefined);
    assert.equal(parsed.settingsFile, undefined);
    assert.equal(parseArgs(['--input-file', 'a', '--output-file', 'b', '--timeout', String(MIN_TIMEOUT_SECONDS)]).timeoutSeconds, MIN_TIMEOUT_SECONDS);
  });

  test('resolveDshBin prefers the flag, then DSH_BIN, then PATH', () => {
    assert.equal(resolveDshBin('/opt/dsh', { DSH_BIN: '/env/dsh' }), '/opt/dsh');
    assert.equal(resolveDshBin(undefined, { DSH_BIN: '/env/dsh' }), '/env/dsh');
    assert.equal(resolveDshBin(undefined, {}), 'dsh');
    assert.throws(() => resolveDshBin('  ', {}), /must not be blank/);
  });

  test('isEntrypoint matches only the same file', () => {
    const self = fileURLToPath(import.meta.url);
    assert.equal(isEntrypoint(`file://${self}`, self), true);
    assert.equal(isEntrypoint(`file://${self}`, '/tmp/other.mjs'), false);
    assert.equal(isEntrypoint(`file://${self}`, undefined), false);
  });
});

describe('request validation', () => {
  test('accepts the documented select shape', () => {
    assert.equal(validateRequest(defaultRequest()).operation, 'select');
    const extended = defaultRequest({
      annotations: [{ profileId: 'p1', text: 'Useful for this workflow', revision: 3, updatedAt: '2026-09-25T00:00:00Z' }],
      routingPreferences: [{ match: { adapter: 'dsh', effort: 'off' }, reason: 'Try this route first' }],
    });
    assert.equal(validateRequest(extended).routingPreferences[0].match.effort, 'off');
  });

  test('rejects malformed requests with a stable code', () => {
    const cases = [
      ['not an object', /request document must be a JSON object/],
      [{ ...defaultRequest(), operation: 'delete' }, /"operation"/],
      [{ ...defaultRequest(), requestId: '' }, /"requestId"/],
      [{ ...defaultRequest(), profile: { provider: 'p', model: 'm' } }, /"profile" must carry/],
      [{ ...defaultRequest(), tableRevision: -1 }, /"tableRevision"/],
      [{ ...defaultRequest(), tableRevision: 1.5 }, /"tableRevision"/],
      [{ ...defaultRequest(), profiles: [] }, /"profiles" must hold at least/],
      [{ ...defaultRequest(), profiles: [{ profileId: 'a' }, { profileId: 'a' }] }, /repeats a/],
      [{ ...defaultRequest(), task: undefined }, /"task" must be bounded/],
      [{ ...defaultRequest(), evidence: 'nope' }, /"evidence" must be an array/],
      [{ ...defaultRequest(), routingPreferences: [{ match: {}, reason: 'empty' }] }, /routingPreferences require/],
      [{ ...defaultRequest(), routingPreferences: Array(9).fill({ match: { model: 'm' }, reason: 'x' }) }, /routingPreferences/],
      [{ ...defaultRequest(), annotations: [{ profileId: 'p1', text: '', revision: 1, updatedAt: 'now' }] }, /annotations require/],
      [{ ...defaultRequest(), unexpected: 1 }, /unexpected request field/],
    ];
    for (const [value, pattern] of cases) {
      assert.throws(() => validateRequest(value), pattern, JSON.stringify(value).slice(0, 120));
    }
  });

  test('malformed input files produce an error envelope and exit 1', () => {
    const invalid = scenario({ request: '{ not json' });
    const result = runDecisionCli(invalid);
    assert.equal(result.status, 1, result.stderr);
    const envelope = readEnvelope(invalid);
    assert.equal(envelope.status, 'error');
    assert.equal(envelope.code, 'input-invalid-json');
    assert.equal(envelope.shutdownConfirmed, true);
    assert.equal(result.stdout, '');
  });

  test('a missing input file produces an error envelope and exit 1', () => {
    const s = scenario();
    rmSync(s.inputFile, { force: true });
    const result = runDecisionCli(s);
    assert.equal(result.status, 1);
    assert.equal(readEnvelope(s).code, 'input-unreadable');
  });

  test('a missing --settings-file is a usage error before any spawn', () => {
    // Deliberately passed by flag only: the environment carries no fallback, so
    // this exercises the adapter's own existence check.
    const settingsFile = join(root, 'does-not-exist.yaml');
    const s = scenario();
    const result = spawnSync(process.execPath, [CLI_PATH, ...s.args, '--settings-file', settingsFile], { env: s.env, encoding: 'utf8' });
    assert.equal(result.status, 2, result.stderr);
    assert.match(result.stderr, /--settings-file/);
    // A configuration failure is still reported as one structured envelope,
    // never as an invented success, and no child is started at all.
    assert.deepEqual(readEnvelope(s), {
      status: 'error',
      code: 'settings-file-missing',
      message: `--settings-file ${settingsFile} does not exist`,
      elapsedSeconds: 0,
      shutdownConfirmed: true,
    });
    assert.equal(existsSync(join(s.records, 'argv.json')), false, 'no child may be spawned for a usage error');
  });
});

describe('select success path', () => {
  test('writes the documented envelope and maps parameters into the child', () => {
    const s = scenario();
    const result = runDecisionCli(s);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, '', 'stdout must stay clean');
    const envelope = readEnvelope(s);
    assert.deepEqual(Object.keys(envelope), [
      'status', 'operation', 'tableRevision', 'requested', 'resolved', 'observed', 'usage', 'elapsedSeconds', 'shutdownConfirmed', 'decision',
    ]);
    assert.equal(envelope.status, 'ok');
    assert.equal(envelope.operation, 'select');
    assert.equal(envelope.tableRevision, 7);
    assert.deepEqual(envelope.requested, { provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'off' });
    assert.deepEqual(envelope.resolved, { provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'off' });
    assert.equal(envelope.observed, null, 'the adapter must not invent observed identity');
    assert.deepEqual(envelope.usage, { inputTokens: 11, outputTokens: 7, totalTokens: 18, cacheReadTokens: 0, cacheWriteTokens: null, reasoningTokens: null });
    assert.equal(envelope.shutdownConfirmed, true);
    assert.deepEqual(Object.keys(envelope.decision), ['profileId', 'reason', 'evidenceIds', 'policyCheck', 'support']);
    assert.equal(envelope.decision.profileId, 'p1');
    assert.deepEqual(envelope.decision.evidenceIds, []);
    assert.deepEqual(
      envelope.decision.policyCheck,
      { hardConstraints: {}, taskPreference: { ruleIndex: null, outcome: 'none' }, userPreference: 'none' },
    );
    assert.deepEqual(envelope.decision.support, { cardProfileIds: [], annotationProfileIds: [] });
    // The child received the profile, the private request path, and a bounded ms timeout.
    assert.equal(readRecordText(s, 'profile.txt'), 'headless');
    const request = readRecord(s, 'request.json');
    assert.equal(request.profile.effort, 'off');
    const patch = readRecord(s, 'patch.json');
    const disabled = patch.filter((row) => row.disabled === true).map((row) => row.id);
    assert.deepEqual(disabled, ['headless-runner', 'session-telemetry-otel', 'session-title-llm']);
    const insert = patch.find((row) => Array.isArray(row.insert));
    assert.equal(insert.insert[0].name, PLUGIN_PATH);
    assert.ok(Number.isSafeInteger(insert.insert[0].config.timeoutMs));
    assert.equal(insert.insert[0].config.inputFile, join(dirname(insert.insert[0].config.inputFile), 'request.json'));
    assert.equal(result.stdout, '');
  });

  test('the child is spawned in a private run directory that is removed afterwards', () => {
    const s = scenario();
    const result = runDecisionCli(s);
    assert.equal(result.status, 0, result.stderr);
    const argv = readRecord(s, 'argv.json');
    assert.ok(argv.cwd.includes('deepseek-delegate-decision-'), `unexpected cwd ${argv.cwd}`);
    assert.equal(existsSync(argv.cwd), false, 'the private run directory must be removed');
    assert.deepEqual(argv.argv.slice(0, 5), ['--profile', 'headless', '--patch', argv.argv[3], '--']);
    assert.deepEqual(argv.argv.slice(-3), ['--', '--', 'deepseek-delegate-decision-adapter']);
  });

  test('an abstention is preserved as a null profileId, not invented', () => {
    const s = scenario({
      answer: JSON.stringify({ decision: { profileId: null, reason: 'no candidate is clearly better supported', evidenceIds: [] } }),
    });
    const result = runDecisionCli(s);
    assert.equal(result.status, 0, result.stderr);
    const envelope = readEnvelope(s);
    assert.equal(envelope.decision.profileId, null);
    assert.match(envelope.decision.reason, /no candidate/);
  });

  test('--settings-file is passed to the child as the settings row path', () => {
    const settingsFile = join(root, 'settings', 'custom.yaml');
    mkdirSync(dirname(settingsFile), { recursive: true });
    writeFileSync(settingsFile, 'agent-default-model:\n  provider: deepseek-official\n  model: deepseek-flash\n');
    const s = scenario({ settingsFile, argv: ['--settings-file', settingsFile] });
    const result = runDecisionCli(s);
    assert.equal(result.status, 0, result.stderr);
    const patch = readRecord(s, 'patch.json');
    const settingsRow = patch.find((row) => row.id === 'settings');
    assert.deepEqual(settingsRow.config, { path: settingsFile });
  });
});

describe('bounded output validation', () => {
  test('a selected profile outside the enabled available candidates is refused', () => {
    const s = scenario({ answer: JSON.stringify({ decision: { profileId: 'p2', reason: 'looks fine', evidenceIds: [] } }) });
    const result = runDecisionCli(s);
    assert.equal(result.status, 1);
    const envelope = readEnvelope(s);
    assert.equal(envelope.status, 'error');
    assert.equal(envelope.code, 'decision-not-candidate');
    assert.equal(envelope.shutdownConfirmed, true);
  });

  test('evidence outside the supplied set is refused', () => {
    const s = scenario({ answer: JSON.stringify({ decision: { profileId: 'p1', reason: 'ok', evidenceIds: ['invented'] } }) });
    const result = runDecisionCli(s);
    assert.equal(result.status, 1);
    assert.equal(readEnvelope(s).code, 'decision-evidence-unknown');
  });

  test('an abstention that cites evidence is refused', () => {
    const s = scenario({ answer: JSON.stringify({ decision: { profileId: null, reason: 'abstain', evidenceIds: ['e1'] } }) });
    const result = runDecisionCli(s);
    assert.equal(result.status, 1);
    assert.equal(readEnvelope(s).code, 'decision-evidence-unknown');
  });

  test('unknown answer fields are refused as protocol errors', () => {
    const extra = scenario({ answer: JSON.stringify({ decision: { profileId: 'p1', reason: 'ok', evidenceIds: [], temperature: 1 } }) });
    assert.equal(runDecisionCli(extra).status, 1);
    assert.equal(readEnvelope(extra).code, 'child-protocol-error');
  });

  test('a stated fallback while a legal preference match exists is refused', () => {
    // The historical inversion: a positive rule matching p1 must be stated as
    // matched, never as an avoid/fallback while the candidate is legal.
    const request = defaultRequest({
      routingPreferences: [{ match: { provider: 'deepseek-official' }, reason: 'Prefer the installed provider' }],
    });
    request.policyFacts = derivePolicyFacts({
      profiles: request.profiles, routingPreferences: request.routingPreferences, preferences: [], hardConstraints: {},
    });
    const s = scenario({ request, answer: JSON.stringify({
      decision: {
        profileId: 'p1', reason: 'avoiding the preference', evidenceIds: [],
        policyCheck: { hardConstraints: {}, taskPreference: { ruleIndex: 0, outcome: 'fallback' }, userPreference: 'none' },
        support: { cardProfileIds: [], annotationProfileIds: [] },
      },
    }) });
    const result = runDecisionCli(s);
    assert.equal(result.status, 1);
    assert.equal(readEnvelope(s).code, 'policy-outcome-false');
  });

  test('an invented hard constraint is refused', () => {
    const s = scenario({ answer: JSON.stringify({
      decision: {
        profileId: 'p1', reason: 'assuming a dsh constraint', evidenceIds: [],
        policyCheck: { hardConstraints: { adapter: 'dsh' }, taskPreference: { ruleIndex: null, outcome: 'none' }, userPreference: 'none' },
        support: { cardProfileIds: [], annotationProfileIds: [] },
      },
    }) });
    const result = runDecisionCli(s);
    assert.equal(result.status, 1);
    assert.equal(readEnvelope(s).code, 'policy-constraint-mismatch');
  });

  test('an unsupported alternative and unknown support references are refused', () => {
    const request = defaultRequest({
      routingPreferences: [{ match: { model: 'deepseek-flash' }, reason: 'Prefer the flash model' }],
      annotations: [{ profileId: 'p1', text: 'user annotation', revision: 1, updatedAt: '2026-09-25T00:00:00Z' }],
    });
    request.policyFacts = derivePolicyFacts({
      profiles: request.profiles, routingPreferences: request.routingPreferences, preferences: [], hardConstraints: {},
    });
    // p1 is the matching candidate, so selecting it is 'matched'; selecting p3
    // (disabled, hence not legal) is refused as a non-candidate before policy.
    const stated = (profileId, outcome) => JSON.stringify({
      decision: {
        profileId, reason: 'deviating', evidenceIds: [],
        policyCheck: { hardConstraints: {}, taskPreference: { ruleIndex: 0, outcome }, userPreference: 'none' },
        support: { cardProfileIds: [], annotationProfileIds: [] },
      },
    });
    // A supported alternative: selecting p1 with outcome alternative is false
    // (p1 IS the match), so the false outcome is what gets refused here.
    const falseOutcome = scenario({ request, answer: stated('p1', 'alternative') });
    assert.equal(runDecisionCli(falseOutcome).status, 1);
    assert.equal(readEnvelope(falseOutcome).code, 'policy-outcome-false');
    // Unknown support references are refused even when the outcome is honest.
    const unknownSupport = scenario({ request, answer: JSON.stringify({
      decision: {
        profileId: 'p1', reason: 'deviating with invented support', evidenceIds: [],
        policyCheck: { hardConstraints: {}, taskPreference: { ruleIndex: 0, outcome: 'matched' }, userPreference: 'none' },
        support: { cardProfileIds: ['ghost'], annotationProfileIds: [] },
      },
    }) });
    assert.equal(runDecisionCli(unknownSupport).status, 1);
    assert.equal(readEnvelope(unknownSupport).code, 'policy-support-unknown');
  });

  test('a request whose policy facts disagree with its table never reaches a child', () => {
    const request = defaultRequest();
    request.policyFacts = { ...request.policyFacts, userPreferredProfileIds: ['p1'] };
    const s = scenario({ request });
    const result = runDecisionCli(s);
    assert.equal(result.status, 1);
    assert.equal(readEnvelope(s).code, 'policy-facts-mismatch');
    assert.equal(existsSync(join(s.records, 'argv.json')), false, 'no child may be spawned');
    assert.equal(existsSync(join(s.records, 'dump-config-seen.txt')), false, 'not even the boot gate runs');
  });

  test('policy facts must carry the documented bounded shape', () => {
    const cases = [
      [defaultRequest({ policyFacts: null }), /"policyFacts" must be an object/],
      [defaultRequest({ policyFacts: { hardConstraints: {} } }), /taskPreference/],
      [defaultRequest({ policyFacts: { hardConstraints: { budget: 'low' }, taskPreference: { ruleIndex: null, matchingProfileIds: [] }, userPreferredProfileIds: [] } }), /hardConstraints/],
      [defaultRequest({ policyFacts: { hardConstraints: {}, taskPreference: { ruleIndex: -1, matchingProfileIds: [] }, userPreferredProfileIds: [] } }), /ruleIndex/],
      [defaultRequest({ policyFacts: { hardConstraints: {}, taskPreference: { ruleIndex: null, matchingProfileIds: ['p1', 'p1'] }, userPreferredProfileIds: [] } }), /matchingProfileIds/],
      [defaultRequest({ policyFacts: { hardConstraints: {}, taskPreference: { ruleIndex: null, matchingProfileIds: [] }, userPreferredProfileIds: 'p1' } }), /userPreferredProfileIds/],
      [{ ...defaultRequest(), extraFacts: true }, /unexpected request field/],
    ];
    for (const [value, pattern] of cases) {
      assert.throws(() => validateRequest(value), pattern, JSON.stringify(value.policyFacts ?? value));
    }
  });
});

describe('child failure handling', () => {
  test('a structured child failure becomes a structured, nonzero result', () => {
    const s = scenario({ scenarioName: 'error', code: 'answer-tool-call' });
    const result = runDecisionCli(s);
    assert.equal(result.status, 1);
    const envelope = readEnvelope(s);
    assert.equal(envelope.status, 'error');
    assert.equal(envelope.code, 'answer-tool-call');
    assert.equal(envelope.operation, 'select');
    assert.equal(envelope.shutdownConfirmed, true);
  });

  test('a child that exits without a result is reported, never invented', () => {
    const s = scenario({ scenarioName: 'no-result' });
    const result = runDecisionCli(s);
    assert.equal(result.status, 1);
    const envelope = readEnvelope(s);
    assert.equal(envelope.code, 'child-no-result');
    assert.equal(envelope.status, 'error');
  });

  test('a rejected dsh invocation is classified', () => {
    const s = scenario({ scenarioName: 'usage' });
    const result = runDecisionCli(s);
    assert.equal(result.status, 1);
    assert.equal(readEnvelope(s).code, 'dsh-invocation-failed');
  });

  test('a launcher that cannot start exits 4 without claiming shutdown', () => {
    const s = scenario();
    const result = spawnSync(process.execPath, [CLI_PATH, ...s.args, '--dsh-bin', join(s.dir, 'missing-dsh')], { env: s.env, encoding: 'utf8' });
    assert.equal(result.status, 4, result.stderr);
    const envelope = readEnvelope(s);
    assert.equal(envelope.code, 'dsh-unavailable');
    assert.equal(envelope.shutdownConfirmed, false, 'an unstarted launcher must not be reported as stopped');
  });
});

describe('fail-closed boot safety gate', () => {
  test('an active headless runner is refused before any real boot', () => {
    const s = scenario({ dump: 'runner-active' });
    const result = runDecisionCli(s);
    assert.equal(result.status, 1, result.stderr);
    const envelope = readEnvelope(s);
    assert.equal(envelope.code, 'decision-profile-unsafe');
    assert.equal(envelope.details.reason, 'runner-not-disabled');
    // The gate runs BEFORE the real invocation: the stub reached its dump
    // branch and the request file was never read by a decision run.
    assert.equal(existsSync(join(s.records, 'dump-config-seen.txt')), true);
    assert.equal(existsSync(join(s.records, 'request.json')), false, 'no decision child may run');
  });

  test('a missing headless-runner row is refused, never assumed disabled', () => {
    const s = scenario({ dump: 'runner-missing' });
    const result = runDecisionCli(s);
    assert.equal(result.status, 1, result.stderr);
    assert.equal(readEnvelope(s).code, 'decision-profile-unsafe');
    assert.equal(readEnvelope(s).details.reason, 'runner-row-missing');
    assert.equal(existsSync(join(s.records, 'request.json')), false);
  });

  test('a harness home without the installed profile is refused, not created', () => {
    const s = scenario({ installProfile: false });
    const result = runDecisionCli(s);
    assert.equal(result.status, 1, result.stderr);
    assert.equal(readEnvelope(s).code, 'decision-profile-missing');
    // Booting a missing profile would make dsh create it: never write global config.
    assert.equal(existsSync(join(s.dshHome, 'profiles', 'headless', 'package.json')), false);
  });

  test('the gate inspects the composed tree the adapter itself built', () => {
    const s = scenario();
    const result = runDecisionCli(s);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(join(s.records, 'dump-config-seen.txt')), true);
    const patch = readRecord(s, 'patch.json');
    assert.deepEqual(
      patch.filter((row) => row.disabled === true).map((row) => row.id),
      ['headless-runner', 'session-telemetry-otel', 'session-title-llm'],
    );
  });
});

describe('timeout and cancel cleanup', () => {
  test('a hanging child is terminated and honestly reported', () => {
    const s = scenario({ scenarioName: 'hang', timeout: MIN_TIMEOUT_SECONDS });
    const started = Date.now();
    const result = runDecisionCli(s, { timeoutMs: 40_000 });
    const elapsed = (Date.now() - started) / 1_000;
    assert.equal(result.status, 3, result.stderr);
    const envelope = readEnvelope(s);
    assert.equal(envelope.status, 'error');
    assert.equal(envelope.code, 'timeout');
    assert.equal(envelope.shutdownConfirmed, true, 'the reaped child is confirmed stopped');
    assert.ok(elapsed < MIN_TIMEOUT_SECONDS + 10, `timeout took ${String(elapsed)}s`);
  });

  test('SIGTERM cancels the run, kills the owned child and reports confirmed shutdown', async () => {
    const s = scenario({ scenarioName: 'hang', timeout: MAX_TIMEOUT_SECONDS });
    const child = spawn(process.execPath, [CLI_PATH, ...cliArgs(s.args)], { env: s.env, stdio: ['ignore', 'pipe', 'pipe'] });
    const stdout = [];
    const stderr = [];
    child.stdout.on('data', (chunk) => stdout.push(chunk));
    child.stderr.on('data', (chunk) => stderr.push(chunk));
    const done = new Promise((resolve) => child.on('close', (code, signal) => resolve({ code, signal })));
    await waitForFile(join(s.records, 'argv.json'), 20_000);
    child.kill('SIGTERM');
    const finished = await done;
    assert.equal(finished.signal, null, `the adapter must exit through its own path, got ${String(finished.signal)}`);
    assert.equal(finished.code, 1, Buffer.concat(stderr).toString('utf8'));
    const envelope = readEnvelope(s);
    assert.equal(envelope.status, 'error');
    assert.equal(envelope.code, 'cancelled');
    assert.equal(envelope.shutdownConfirmed, true);
    assert.equal(Buffer.concat(stdout).toString('utf8'), '');
  });

  test('a child that ignores SIGTERM is still reaped and confirmed stopped', async () => {
    const { runChild } = await import('../scripts/decision.mjs');
    const result = await runChild({
      dshBin: process.execPath,
      argv: ['-e', 'process.on("SIGTERM", () => {}); setInterval(() => {}, 1000)'],
      env: process.env,
      cwd: root,
      timeoutMs: 1_000,
    });
    assert.equal(result.status, 'timeout');
    assert.equal(result.shutdownConfirmed, true, 'the reaped process is confirmed stopped');
  });

  test('the owned process group is signalled, so a group member stops too', async () => {
    const { runChild } = await import('../scripts/decision.mjs');
    // The launcher hangs and leaves a NON-detached grandchild in its group.
    // Signalling only the direct child would leave that grandchild running
    // while still claiming a confirmed shutdown.
    const result = await runChild({
      dshBin: process.execPath,
      argv: ['-e', [
        'const { spawn } = require("node:child_process");',
        'const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });',
        'console.log(String(child.pid));',
        'setInterval(() => {}, 1000);',
      ].join(' ')],
      env: process.env,
      cwd: root,
      timeoutMs: 1_000,
    });
    const survivor = Number(result.stdout.trim().split('\n').at(-1));
    assert.ok(Number.isSafeInteger(survivor) && survivor > 0, `no group member pid: ${result.stdout}`);
    try {
      assert.equal(alive(survivor), false, 'a member of the owned group must not outlive the run');
      assert.equal(result.status, 'timeout');
      assert.equal(result.shutdownConfirmed, true, 'a proven-gone group is a confirmed shutdown');
    } finally {
      killQuietly(survivor);
    }
  });

  test('a launcher that leaves a group member running is never called stopped', async () => {
    const { runChild } = await import('../scripts/decision.mjs');
    // The launcher exits zero immediately but leaves a live, non-detached
    // group member behind. A result document plus a reaped direct child is NOT
    // proof that the owned work stopped.
    const result = await runChild({
      dshBin: process.execPath,
      argv: ['-e', [
        'const { spawn } = require("node:child_process");',
        'const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });',
        'console.log(String(child.pid));',
        'child.unref();',
      ].join(' ')],
      env: process.env,
      cwd: root,
      timeoutMs: 30_000,
    });
    const survivor = Number(result.stdout.trim().split('\n').at(-1));
    assert.ok(Number.isSafeInteger(survivor) && survivor > 0, `no group member pid: ${result.stdout}`);
    try {
      assert.equal(result.shutdownConfirmed, false, 'a live owned process means shutdown is unproven');
      assert.equal(alive(survivor), true, 'the survivor is what makes the claim unprovable');
    } finally {
      killQuietly(survivor);
    }
  });

  test('a child that escapes into its own group is not claimed as ours', async () => {
    const { runChild } = await import('../scripts/decision.mjs');
    // A launcher may explicitly detach work into a group of its own. That work
    // is outside this adapter's ownership, so nothing of OURS remains and the
    // shutdown of the owned group is confirmed; the escapee is reported by
    // neither as ours nor as stopped by us.
    const result = await runChild({
      dshBin: process.execPath,
      argv: ['-e', [
        'const { spawn } = require("node:child_process");',
        'const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { detached: true, stdio: "ignore" });',
        'console.log(String(child.pid));',
        'child.unref();',
      ].join(' ')],
      env: process.env,
      cwd: root,
      timeoutMs: 30_000,
    });
    const escaped = Number(result.stdout.trim().split('\n').at(-1));
    try {
      assert.equal(result.shutdownConfirmed, true, 'the owned group is gone');
      assert.equal(alive(escaped), true, 'the escaped process was never in the owned group');
    } finally {
      killQuietly(escaped);
    }
  });
});

/** Whether one process id is still present. */
function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === 'EPERM';
  }
}

/** Best-effort cleanup so a test never leaks a process. */
function killQuietly(pid) {
  try {
    process.kill(pid, 'SIGKILL');
  } catch { /* already gone */ }
}

/** Wait for one file to appear, bounded. */
async function waitForFile(file, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (existsSync(file)) return;
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${file}`);
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
}
