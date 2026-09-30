/**
 * Unit tests for the deepseek-delegate session-capture observer plugin.
 *
 * These tests use the event and session shapes discovered in the installed dsh
 * sources and verified against a real `@deepseek-ai/dsh-session` store (see
 * `tests/manual/real-observer.mjs`):
 * - `ctx.on('session/event', (session, event) => ...)`
 * - root session: `session.header` with `cwd` and no parent/origin/depth
 * - user message event: `event.type === 'user/message'` and `event.data` is the
 *   `UserMessage` itself (`content: [{ type: 'text', text }]`,
 *   `source: { kind: 'user' }`).
 *
 * The plugin must never throw into the run and must never capture a child,
 * unrelated, or mismatching session.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, before, describe, test } from 'node:test';
import { modeOf, readText } from './support/helpers.mjs';

const PLUGIN_URL = new URL('../plugins/session-capture.mjs', import.meta.url);
const PLUGIN_PATH = fileURLToPath(PLUGIN_URL);
const { apply, name } = await import(PLUGIN_URL.href);

const PROMPT = 'Do the bounded thing.\n';
const PROMPT_SHA256 = createHash('sha256').update(PROMPT, 'utf8').digest('hex');

let root;
before(() => {
  root = mkdtempSync(join(tmpdir(), 'deepseek-delegate-observer-'));
});
after(() => {
  rmSync(root, { recursive: true, force: true });
});

/** Minimal Cordis-like context: records listeners and lets a test emit. */
function fakeContext() {
  const listeners = new Map();
  return {
    on(event, listener) {
      const list = listeners.get(event) ?? [];
      list.push(listener);
      listeners.set(event, list);
    },
    emit(event, ...args) {
      for (const listener of listeners.get(event) ?? []) listener(...args);
    },
  };
}

/** Root session shape as created by `ctx.sessions.create(id, { meta: { cwd } })`. */
function rootSession(id, cwd) {
  return {
    id,
    header: { version: 3, id, createdAt: 0, cwd, isSeeded: false },
  };
}

/** Real `user/message` event shape: `data` is the UserMessage itself. */
function userMessageEvent(text, seq = 4, source = { kind: 'user' }) {
  return {
    type: 'user/message',
    seq,
    time: 0,
    data: {
      id: `message-${seq}`,
      role: 'user',
      content: [{ type: 'text', text }],
      source,
    },
    surfaceOp: 'append',
  };
}

function captureFile(context, dir, config = {}) {
  const capturePath = join(dir, 'capture.json');
  apply(context, { capturePath, promptSha256: PROMPT_SHA256, cwd: dir, ...config });
  return capturePath;
}

describe('session-capture observer', () => {
  test('plugin is dependency-free: node builtins only', () => {
    const source = readText(PLUGIN_PATH);
    const specifiers = [...source.matchAll(/^import\s[^\n]*from\s+'([^']+)'/gm)].map((match) => match[1]);
    assert.ok(specifiers.length > 0, 'plugin has imports');
    for (const specifier of specifiers) {
      assert.ok(specifier.startsWith('node:'), `unexpected dependency: ${specifier}`);
    }
    assert.equal(name, 'deepseek-delegate-session-capture');
  });

  test('captures the exact root session id for this run prompt', () => {
    const dir = mkdtempSync(join(root, 'capture-'));
    const ctx = fakeContext();
    const capturePath = captureFile(ctx, dir);
    ctx.emit('session/event', rootSession('session-root-1', dir), userMessageEvent(PROMPT));

    assert.equal(existsSync(capturePath), true);
    const record = JSON.parse(readFileSync(capturePath, 'utf8'));
    assert.equal(record.version, 1);
    assert.equal(record.sessionId, 'session-root-1');
    assert.equal(record.cwd, dir);
    assert.equal(record.promptSha256, PROMPT_SHA256);
    assert.equal(record.seq, 4);
    assert.equal(modeOf(capturePath), '600', 'capture metadata is owner-private');
  });

  test('ignores a different prompt (never guesses by order or time)', () => {
    const dir = mkdtempSync(join(root, 'other-prompt-'));
    const ctx = fakeContext();
    const capturePath = captureFile(ctx, dir);
    ctx.emit('session/event', rootSession('session-root-2', dir), userMessageEvent('Something else entirely.\n'));
    assert.equal(existsSync(capturePath), false);
  });

  test('ignores non-user sources and non-user message events', () => {
    const dir = mkdtempSync(join(root, 'sources-'));
    const ctx = fakeContext();
    const capturePath = captureFile(ctx, dir);
    const session = rootSession('session-root-3', dir);
    ctx.emit('session/event', session, userMessageEvent(PROMPT, 1, { kind: 'plugin', plugin: 'other' }));
    ctx.emit('session/event', session, userMessageEvent(PROMPT, 2, { kind: 'subagent' }));
    ctx.emit('session/event', session, { type: 'assistant/message', seq: 3, time: 0, data: {} });
    ctx.emit('session/event', session, { type: 'turn/start', seq: 4, time: 0, data: { turn: 1 } });
    assert.equal(existsSync(capturePath), false);
  });

  test('ignores child sessions: fork parent, subagent origin, or delegation depth', () => {
    for (const [label, header] of [
      ['fork', { parentSession: 'session-parent' }],
      ['origin', { origin: 'subagent' }],
      ['depth', { delegationDepth: 1 }],
    ]) {
      const dir = mkdtempSync(join(root, `child-${label}-`));
      const ctx = fakeContext();
      const capturePath = captureFile(ctx, dir);
      const session = { id: `session-child-${label}`, header: { version: 3, cwd: dir, isSeeded: false, ...header } };
      ctx.emit('session/event', session, userMessageEvent(PROMPT));
      assert.equal(existsSync(capturePath), false, `${label} session must not be captured`);
    }
  });

  test('ignores a session created in another canonical cwd', () => {
    const dir = mkdtempSync(join(root, 'cwd-mismatch-'));
    const ctx = fakeContext();
    const capturePath = captureFile(ctx, dir);
    ctx.emit('session/event', rootSession('session-root-4', `${dir}-elsewhere`), userMessageEvent(PROMPT));
    assert.equal(existsSync(capturePath), false);
  });

  test('joins text blocks exactly like the CLI hashes the delivered prompt', () => {
    const dir = mkdtempSync(join(root, 'blocks-'));
    const ctx = fakeContext();
    const promptSha256 = createHash('sha256').update('part one part two', 'utf8').digest('hex');
    const capturePath = captureFile(ctx, dir, { promptSha256 });
    ctx.emit('session/event', rootSession('session-root-5', dir), {
      type: 'user/message',
      seq: 1,
      time: 0,
      data: {
        id: 'message-blocks',
        role: 'user',
        content: [{ type: 'text', text: 'part one ' }, { type: 'text', text: 'part two' }],
        source: { kind: 'user' },
      },
      surfaceOp: 'append',
    });
    assert.equal(JSON.parse(readFileSync(capturePath, 'utf8')).sessionId, 'session-root-5');
  });

  test('two matching root identities invalidate capture instead of choosing the first', () => {
    const dir = mkdtempSync(join(root, 'first-wins-'));
    const ctx = fakeContext();
    const capturePath = captureFile(ctx, dir);
    ctx.emit('session/event', rootSession('session-root-6', dir), userMessageEvent(PROMPT, 1));
    ctx.emit('session/event', rootSession('session-root-7', dir), userMessageEvent(PROMPT, 2));
    assert.equal(JSON.parse(readFileSync(capturePath, 'utf8')).ambiguous, true);
    assert.equal(JSON.parse(readFileSync(capturePath, 'utf8')).sessionId, undefined);
    assert.deepEqual(readdirSync(dir), ['capture.json']);
  });

  test('malformed events, sessions, and config never throw or write', () => {
    const dir = mkdtempSync(join(root, 'malformed-'));
    const ctx = fakeContext();
    const capturePath = captureFile(ctx, dir);
    assert.doesNotThrow(() => {
      ctx.emit('session/event', undefined, undefined);
      ctx.emit('session/event', {}, {});
      ctx.emit('session/event', { id: 'x', header: null }, userMessageEvent(PROMPT));
      ctx.emit('session/event', rootSession('session-root-8', dir), { type: 'user/message', data: { content: 'nope' } });
      ctx.emit('session/event', rootSession('session-root-8', dir), { type: 'user/message', data: { content: [{ type: 'text', text: 42 }], source: { kind: 'user' } } });
    });
    assert.equal(existsSync(capturePath), false);

    // Missing or invalid config simply disables capture instead of breaking boot.
    for (const config of [undefined, {}, { capturePath: '' }, { capturePath: join(dir, 'x.json') }, { promptSha256: 'not-a-hash' }]) {
      const bare = fakeContext();
      assert.doesNotThrow(() => {
        apply(bare, config);
        bare.emit('session/event', rootSession('session-root-9', dir), userMessageEvent(PROMPT));
      });
    }
  });

  test('a write failure never escapes into the run', () => {
    const dir = mkdtempSync(join(root, 'write-failure-'));
    const ctx = fakeContext();
    apply(ctx, { capturePath: join(dir, 'missing-subdir', 'capture.json'), promptSha256: PROMPT_SHA256, cwd: dir });
    assert.doesNotThrow(() => {
      ctx.emit('session/event', rootSession('session-root-10', dir), userMessageEvent(PROMPT));
    });
    assert.equal(existsSync(join(dir, 'missing-subdir', 'capture.json')), false);
  });
});
