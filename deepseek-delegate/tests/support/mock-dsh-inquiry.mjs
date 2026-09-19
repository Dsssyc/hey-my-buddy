#!/usr/bin/env node
/**
 * Mock dsh for the inquiry integration tests.
 *
 * It is a drop-in replacement for the installed `dsh` binary on PATH (`DSH_BIN`):
 * `scripts/run.mjs` spawns it with the real `--profile headless --patch <file> -- -- <prompt>`
 * argv, and this mock mounts the REAL inquiry bridge from that patch over a
 * synthetic agent, then behaves like one headless run. No model is called.
 *
 * MOCK_INQUIRY_* behavior:
 *   MOCK_INQUIRY_HOLD_MS    how long the "run" stays alive (default 1500)
 *   MOCK_INQUIRY_ANSWER_MS  after N ms, claim the newest question and answer it
 *   MOCK_INQUIRY_ANSWER     answer text (default "mock answer")
 *   MOCK_INQUIRY_NO_BIND=1  never emit the identity message (bridge stays unbound)
 *   MOCK_INQUIRY_FINAL      final assistant text printed on stdout
 *   MOCK_INQUIRY_EXIT_CODE  process exit code (default 0)
 */
import { readFileSync } from 'node:fs';

// When this mock is copied elsewhere (as `dsh` on PATH), the shared harness is
// resolved from MOCK_INQUIRY_SUPPORT instead of the sibling path.
const support = process.env.MOCK_INQUIRY_SUPPORT ?? new URL('./fake-headless-run.mjs', import.meta.url).href;
const { createFakeHeadlessRun } = await import(support);

const argv = process.argv.slice(2);
const patchIndex = argv.indexOf('--patch');
if (patchIndex === -1) {
  process.stderr.write('mock dsh: missing --patch\n');
  process.exit(2);
}
const rows = JSON.parse(readFileSync(argv[patchIndex + 1], 'utf8'));
const bridgeRow = rows
  .filter((row) => Array.isArray(row?.insert))
  .flatMap((row) => row.insert)
  .find((entry) => entry.id === 'deepseek-delegate-inquiry-bridge');
if (bridgeRow === undefined) {
  process.stderr.write('mock dsh: the inquiry bridge was not mounted\n');
  process.exit(3);
}

// The prompt is the last argv element, exactly as run.mjs delivers it.
const prompt = argv[argv.length - 1];
const run = await createFakeHeadlessRun({
  prompt,
  cwd: bridgeRow.config.cwd,
  socketPath: bridgeRow.config.socketPath,
  token: bridgeRow.config.token,
  resultsPath: bridgeRow.config.resultsPath,
  bind: process.env.MOCK_INQUIRY_NO_BIND !== '1',
});

const answerMs = Number(process.env.MOCK_INQUIRY_ANSWER_MS ?? 0);
if (answerMs > 0) {
  setTimeout(() => { void run.answerLatest(process.env.MOCK_INQUIRY_ANSWER ?? 'mock answer'); }, answerMs);
}

const holdMs = Number(process.env.MOCK_INQUIRY_HOLD_MS ?? 1500);
setTimeout(async () => {
  await run.started.close();
  process.stdout.write(`${process.env.MOCK_INQUIRY_FINAL ?? 'mock dsh completed'}\n`, () => {
    process.exit(Number(process.env.MOCK_INQUIRY_EXIT_CODE ?? 0));
  });
}, holdMs);
