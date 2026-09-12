#!/usr/bin/env node
/**
 * Manual, opt-in validation of `plugins/session-capture.mjs` against a REAL
 * installed dsh, with no model call, no dsh profile boot, and no writes to dsh
 * global config/storage.
 *
 * It mounts the real `@deepseek-ai/dsh-session` store in a bare Cordis context,
 * registers the observer with the same config the CLI writes into its patch,
 * appends the verified real `user/message` event shape, and checks the capture
 * file. This is the "isolated config/no-model harness" companion to the unit
 * tests, which use the recorded event shapes without needing dsh installed.
 *
 * Usage:
 *   node tests/manual/real-observer.mjs --dsh-lib <path to .../@deepseek-ai>
 *
 * The path is the directory containing the installed @deepseek-ai packages,
 * normally `<dsh-install>/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai`.
 * Nothing is written outside a private temporary directory.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    if (!key?.startsWith('--')) throw new Error(`unexpected argument: ${key}`);
    values[key.slice(2)] = argv[index + 1];
  }
  return values;
}

const sha256 = (text) => createHash('sha256').update(text, 'utf8').digest('hex');

async function main() {
  const { 'dsh-lib': dshLib } = parseArgs(process.argv.slice(2));
  if (!dshLib) throw new Error('--dsh-lib <path to .../@deepseek-ai> is required');
  const cordisPath = join(dshLib, 'cordis', 'lib', 'index.js');
  const sessionPath = join(dshLib, 'dsh-session', 'lib', 'index.js');
  if (!existsSync(cordisPath) || !existsSync(sessionPath)) {
    throw new Error(`no installed cordis/dsh-session under ${dshLib}`);
  }

  const { Context } = await import(pathToFileURL(cordisPath).href);
  const sessionPkg = await import(pathToFileURL(sessionPath).href);
  const observer = await import(new URL('../../plugins/session-capture.mjs', import.meta.url).href);

  const cwd = mkdtempSync(join(tmpdir(), 'deepseek-delegate-real-observer-'));
  try {
    const prompt = 'Do the bounded thing.\n';
    const capturePath = join(cwd, 'capture.json');
    const ctx = new Context();
    await ctx.plugin(sessionPkg.default);
    await ctx.plugin(observer, { capturePath, promptSha256: sha256(prompt), cwd });

    const sessions = ctx.get('sessions');
    const rootId = sessionPkg.SessionId('session-real-observer-root');
    const root = sessions.create(rootId, { meta: { cwd } });
    root.append('user/message', {
      id: 'message-real-1',
      role: 'user',
      content: [{ type: 'text', text: prompt }],
      source: { kind: 'user' },
    }, { surfaceOp: 'append' });

    assert.equal(existsSync(capturePath), true, 'observer wrote capture metadata for the root session');
    const capture = JSON.parse(readFileSync(capturePath, 'utf8'));
    assert.equal(capture.sessionId, String(rootId));
    assert.equal(capture.cwd, cwd);
    assert.equal(capture.promptSha256, sha256(prompt));

    // A child session with the same prompt and cwd must never be captured.
    const childPath = join(cwd, 'child-capture.json');
    const childCtx = new Context();
    await childCtx.plugin(sessionPkg.default);
    await childCtx.plugin(observer, { capturePath: childPath, promptSha256: sha256(prompt), cwd });
    const childSessions = childCtx.get('sessions');
    const child = childSessions.create(sessionPkg.SessionId('session-real-observer-child'), {
      meta: { cwd, parentSession: rootId, delegationDepth: 1 },
    });
    child.append('user/message', {
      id: 'message-real-2',
      role: 'user',
      content: [{ type: 'text', text: prompt }],
      source: { kind: 'user' },
    }, { surfaceOp: 'append' });
    assert.equal(existsSync(childPath), false, 'child session is not a capture target');

    process.stdout.write('real-observer: ok (root captured, child ignored)\n');
  } finally {
    rmSync(cwd, { recursive: true, force: true });
  }
}

await main();
