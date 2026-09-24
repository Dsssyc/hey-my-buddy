#!/usr/bin/env node
/**
 * Synthetic dsh launcher for the decision-adapter tests.
 *
 * It never boots a harness, never reads a settings document, and never calls a
 * model. It records the argv and the loader patch the adapter composed, then
 * writes the outcome named by `MOCK_DECISION_SCENARIO` so the adapter's own
 * process handling, envelope validation, and cleanup can be tested exactly.
 *
 * Scenarios:
 * - `ok` (default): write a valid result document for the request operation.
 * - `answer`: write `MOCK_DECISION_ANSWER` as the model's validated answer.
 * - `error`: write an error document with `MOCK_DECISION_CODE`.
 * - `no-result`: exit 0 without writing anything.
 * - `usage`: print an unknown-option usage error and exit 1.
 * - `hang`: stay alive until signalled, so the adapter's timeout path runs.
 * - `exit-early`: exit 3 without writing anything.
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const recordDir = process.env.MOCK_DECISION_DIR;
const scenario = process.env.MOCK_DECISION_SCENARIO ?? 'ok';
const argv = process.argv.slice(2);

/** Record one diagnostic file; recording failures must not change the outcome. */
function record(name, value) {
  if (recordDir === undefined) return;
  try {
    mkdirSync(recordDir, { recursive: true });
    writeFileSync(join(recordDir, name), typeof value === 'string' ? value : `${JSON.stringify(value, null, 2)}\n`);
  } catch { /* the test's own recording is not the adapter's concern */ }
}

record('argv.json', { argv, cwd: process.cwd(), env: { DSH_HOME: process.env.DSH_HOME ?? null, PATH: process.env.PATH ?? null } });

const patchIndex = argv.indexOf('--patch');
const profileIndex = argv.indexOf('--profile');
record('profile.txt', profileIndex < 0 ? '' : (argv[profileIndex + 1] ?? ''));
let patch = [];
if (patchIndex >= 0 && argv[patchIndex + 1] !== undefined) {
  try {
    patch = JSON.parse(readFileSync(argv[patchIndex + 1], 'utf8'));
  } catch (error) {
    record('patch-error.txt', String(error));
  }
}
record('patch.json', patch);

/**
 * Answer `--dump-config` with a composed-row document: exactly the shape the
 * adapter's fail-closed safety gate scans, derived from the patch it was
 * given. `MOCK_DECISION_DUMP` selects a deliberately unsafe tree so the
 * refusal paths are testable without any risk of a real agent.
 */
if (argv.includes('--dump-config')) {
  record('dump-config-seen.txt', 'yes');
  const mode = process.env.MOCK_DECISION_DUMP ?? 'safe';
  const lines = [];
  for (const patchRow of patch) {
    if (Array.isArray(patchRow?.insert)) {
      for (const inserted of patchRow.insert) {
        lines.push(`- id: ${inserted.id}`, `  name: ${inserted.name}`, '  config:', '    outputDir: /tmp/example');
      }
      continue;
    }
    if (typeof patchRow?.id !== 'string') continue;
    if (mode === 'runner-missing' && patchRow.id === 'headless-runner') continue;
    lines.push(`- id: ${patchRow.id}`);
    const keepDisabled = patchRow.disabled === true && !(mode === 'runner-active' && patchRow.id === 'headless-runner');
    if (keepDisabled) lines.push('  disabled: true');
  }
  process.stdout.write(`${lines.join('\n')}\n`);
  process.exit(0);
}

const row = patch.find((entry) => Array.isArray(entry?.insert))?.insert?.[0];
if (row === undefined || row.config === undefined) {
  process.stderr.write('mock-dsh: no decision plugin row in the patch\n');
  process.exit(9);
}
const runDir = row.config.outputDir;
const inputFile = row.config.inputFile;
let request = null;
try {
  request = JSON.parse(readFileSync(inputFile, 'utf8'));
} catch (error) {
  record('request-error.txt', String(error));
}
record('request.json', request);

/** The valid synthetic success payload for one request. */
function successPayload() {
  const answer = process.env.MOCK_DECISION_ANSWER;
  const operation = request?.operation ?? 'select';
  const envelope = {
    status: 'ok',
    operation,
    resolvedConfig: { provider: request?.profile?.provider ?? null, model: request?.profile?.model ?? null, reasoningEffort: request?.profile?.effort ?? null },
    usage: { inputTokens: 11, outputTokens: 7, totalTokens: 18, cacheReadTokens: 0, cacheWriteTokens: null, reasoningTokens: null },
    reasoningBytes: 0,
    elapsedSeconds: 0.4,
  };
  if (answer !== undefined) return { ...envelope, ...JSON.parse(answer) };
  return {
    ...envelope,
    decision: {
      profileId: request?.profiles?.[0]?.profileId ?? 'p1',
      reason: 'shadowed reason',
      evidenceIds: [],
    },
  };
}

switch (scenario) {
  case 'ok':
  case 'answer':
    writeFileSync(join(runDir, 'result.json'), `${JSON.stringify(successPayload(), null, 2)}\n`);
    process.exit(0);
    break;
  case 'error':
    writeFileSync(join(runDir, 'error.json'), `${JSON.stringify({
      status: 'error',
      code: process.env.MOCK_DECISION_CODE ?? 'call-failed',
      detail: { code: process.env.MOCK_DECISION_CODE ?? 'call-failed', message: 'synthetic provider failure' },
    }, null, 2)}\n`);
    process.exit(0);
    break;
  case 'no-result':
    process.exit(0);
    break;
  case 'usage':
    process.stderr.write("error: unknown option '--nope'\n");
    process.exit(1);
    break;
  case 'exit-early':
    process.exit(3);
    break;
  case 'hang':
    process.on('SIGTERM', () => process.exit(143));
    setInterval(() => {}, 1_000);
    break;
  default:
    process.stderr.write(`mock-dsh: unknown scenario ${scenario}\n`);
    process.exit(9);
}
