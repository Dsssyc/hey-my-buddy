/** Shared helpers for the deepseek-delegate CLI tests. */
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  chmodSync, mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const CLI_PATH = fileURLToPath(new URL('../../scripts/run.mjs', import.meta.url));
const MOCK_FIXTURE = fileURLToPath(new URL('./mock-dsh.mjs', import.meta.url));

/**
 * Environment variables the tests own; inherited values are never reused. The
 * NODE_* entries are harness noise (an experimental env-proxy warning) that
 * would otherwise land in every child's stderr.
 */
const MANAGED_ENV = [
  'DSH_HOME', 'DSH_SETTINGS_FILE', 'DSH_BIN',
  'DSH_DELEGATE_MODEL', 'DSH_DELEGATE_PROVIDER', 'DSH_DELEGATE_EFFORT',
  'DSH_WEB_URL', 'DSH_WEB_URL_FILE', 'DSH_WORKSPACE_SOCKET', 'XDG_CONFIG_HOME',
  'MOCK_ARTIFACT_DIR', 'MOCK_MODE', 'MOCK_STDOUT', 'MOCK_STDERR', 'MOCK_EXIT_CODE', 'MOCK_BYTES',
  'MOCK_CAPTURE', 'MOCK_SESSION_ID', 'MOCK_CAPTURE_PROMPT_SHA256', 'MOCK_EXIT_MARKER',
  'NODE_USE_ENV_PROXY', 'NODE_OPTIONS',
];

/**
 * A clean environment for one CLI process: the real dsh locations cannot leak
 * in, and PATH only offers Node plus the system minimums. Tests that need dsh
 * either pass --dsh-bin or prepend a mock bin directory to PATH.
 */
export function testEnv(overrides = {}) {
  const env = { ...process.env };
  for (const key of MANAGED_ENV) delete env[key];
  env.PATH = [dirname(process.execPath), '/usr/bin', '/bin'].join(':');
  return Object.assign(env, overrides);
}

export function makeWorkspace(prefix = 'deepseek-delegate-test-') {
  return mkdtempSync(join(tmpdir(), prefix));
}

export function makeDir(parent, name) {
  const dir = join(parent, name);
  mkdirSync(dir, { recursive: true });
  return dir;
}

/** Write an executable copy of the mock dsh named `name` into `binDir`. */
export function writeMockDsh(binDir, name = 'dsh') {
  mkdirSync(binDir, { recursive: true });
  const target = join(binDir, name);
  writeFileSync(target, readFileSync(MOCK_FIXTURE), { mode: 0o755 });
  chmodSync(target, 0o755);
  return target;
}

export function writeFile(dir, name, content) {
  const target = join(dir, name);
  writeFileSync(target, content);
  return target;
}

/** Run the CLI to completion and return the spawnSync result. */
export function runCli(args, { env = testEnv(), cwd, timeoutMs = 120000 } = {}) {
  return spawnSync(process.execPath, [CLI_PATH, ...args], { cwd, env, encoding: 'utf8', timeout: timeoutMs });
}

/** Start the CLI without waiting, for signal-driven tests. */
export function startCli(args, { env = testEnv(), cwd } = {}) {
  const child = spawn(process.execPath, [CLI_PATH, ...args], { cwd, env });
  const stdout = [];
  const stderr = [];
  child.stdout.on('data', (chunk) => stdout.push(chunk));
  child.stderr.on('data', (chunk) => stderr.push(chunk));
  const done = new Promise((resolve) => {
    child.on('close', (code, signal) => resolve({
      code, signal, stdout: Buffer.concat(stdout).toString('utf8'), stderr: Buffer.concat(stderr).toString('utf8'),
    }));
  });
  return { child, done };
}

export function readText(file) {
  return readFileSync(file, 'utf8');
}

export function readArtifact(artifactsDir, name) {
  return readText(join(artifactsDir, name));
}

export function readArtifactJson(artifactsDir, name) {
  return JSON.parse(readArtifact(artifactsDir, name));
}

export function parsePayload(result) {
  return JSON.parse(result.stdout);
}

export function sha256(file) {
  return createHash('sha256').update(readFileSync(file)).digest('hex');
}

export function modeOf(path) {
  return (statSync(path).mode & 0o777).toString(8);
}

export function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === 'EPERM';
  }
}

export async function waitFor(predicate, description, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (predicate()) return;
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${description}`);
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
}

export async function waitForProcessExit(pid, timeoutMs = 5000) {
  await waitFor(() => !isAlive(pid), `process ${pid} to exit`, timeoutMs);
}
