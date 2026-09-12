/**
 * Isolated unit tests for the local dsh web-host client (`scripts/lib/web-host.mjs`).
 *
 * Every test drives the client in-process with asynchronous calls against the
 * loopback `mock-web` HTTP fixture: no child process, no real dsh, no model, and
 * no credential file. All tokens are synthetic constants, and the launch URL is
 * never read from `~/.config/deepseek-delegate/web-url`.
 */
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  WebRpcError, WebSetupError, adoptSession, connectWebHost, resolveWebTarget, resolveWorkspace,
} from '../scripts/lib/web-host.mjs';
import { HOSTILE_ERROR_MARKERS, startMockWeb } from './support/mock-web.mjs';

const CWD = '/tmp/deepseek-delegate-web-host-cwd';
const OTHER_PATH = '/tmp/deepseek-delegate-web-host-other-cwd';

/** Connect to one freshly started fixture without touching any credential file. */
async function connectFixture(options = {}) {
  const fixture = await startMockWeb(options);
  try {
    const target = resolveWebTarget({ 'dsh-web-url': fixture.launchUrl }, {});
    const host = await connectWebHost(target, { timeoutMs: 5000 });
    return { fixture, host };
  } catch (error) {
    await fixture.close();
    throw error;
  }
}

/** Every RPC method the fixture saw, in order. */
function rpcMethods(fixture) {
  return fixture.requests.filter((entry) => entry.kind === 'rpc').map((entry) => entry.method);
}

describe('launch URL and transport hygiene', () => {
  test('a rejected launch URL path never echoes the credential-bearing path', () => {
    const marker = 'URLPATH_CREDENTIAL_MARKER_31337';
    assert.throws(
      () => resolveWebTarget({ 'dsh-web-url': `http://127.0.0.1:3080/${marker}?token=${marker}` }, {}),
      (error) => {
        assert.ok(error instanceof WebSetupError);
        assert.match(error.message, /must point at the origin root/);
        assert.ok(!error.message.includes(marker), 'diagnostic echoed the rejected path');
        return true;
      },
    );
  });

  test('an off-origin redirect is refused without echoing its Location', async () => {
    const marker = 'REDIRECT_MARKER_5150';
    const fixture = await startMockWeb({
      authMode: 'cross-origin',
      crossOriginLocation: `http://off-origin.invalid/${marker}?token=${marker}`,
    });
    try {
      const target = resolveWebTarget({ 'dsh-web-url': fixture.launchUrl }, {});
      await assert.rejects(
        () => connectWebHost(target, { timeoutMs: 5000 }),
        (error) => {
          assert.ok(error instanceof WebSetupError);
          assert.match(error.message, /redirected authentication off-origin/);
          assert.ok(!error.message.includes(marker), 'diagnostic echoed the redirect Location');
          assert.ok(!error.message.includes('token='), 'diagnostic echoed the redirect query');
          return true;
        },
      );
    } finally {
      await fixture.close();
    }
  });

  test('a hostile remote error message and code never reach the diagnostic', async () => {
    const { fixture, host } = await connectFixture({ sessionCreateMode: 'hostile-error' });
    try {
      const workspace = await resolveWorkspace(host, CWD, { timeoutMs: 5000 });
      await assert.rejects(
        () => adoptSession(host, { sessionId: 'session-existing-42', workspaceId: workspace.id, cwd: CWD }),
        (error) => {
          assert.ok(error instanceof WebRpcError);
          assert.equal(error.code, 'rpc-error', 'an unknown remote code must collapse to rpc-error');
          assert.match(
            error.message,
            /^session\/create failed on the dsh web host at http:\/\/127\.0\.0\.1:\d+ \(rpc-error\)$/,
            'the diagnostic must be fixed local text plus the allowlisted code',
          );
          const serialized = `${error.message}\n${error.code}\n${JSON.stringify(error)}`;
          for (const marker of Object.values(HOSTILE_ERROR_MARKERS)) {
            assert.ok(!serialized.includes(marker), `leaked hostile server content: ${marker}`);
          }
          return true;
        },
      );
    } finally {
      await fixture.close();
    }
  });
});

describe('session adoption guard', () => {
  test('an unknown session is rejected by the read-only page probe before session/create', async () => {
    const unknown = 'session-missing-7';
    const { fixture, host } = await connectFixture();
    try {
      const workspace = await resolveWorkspace(host, CWD, { timeoutMs: 5000 });
      await assert.rejects(
        () => adoptSession(host, { sessionId: unknown, workspaceId: workspace.id, cwd: CWD }),
        (error) => {
          assert.ok(error instanceof WebRpcError);
          assert.equal(error.code, 'session/not-found');
          assert.match(error.message, /^session\/page failed on the dsh web host at http:\/\/127\.0\.0\.1:\d+ \(session\/not-found\)$/);
          assert.ok(!error.message.includes(unknown), 'the fixed diagnostic must not echo the remote message');
          return true;
        },
      );

      const page = fixture.rpcRequests('session/page');
      assert.equal(page.length, 1, 'exactly one existence probe');
      assert.deepEqual(page[0].payload, {
        address: { kind: 'session', sessionId: unknown },
        throughSeq: -1,
        maxMessages: 1,
      });
      assert.equal(page[0].sentinel, true);
      assert.equal(page[0].persisted, false);

      assert.equal(fixture.rpcRequests('session/create').length, 0, 'no mutation for an unknown id');
      assert.equal(fixture.state.sessionCreateCalls, 0);
      assert.equal(fixture.state.sessionCreates.length, 0);
      assert.equal(fixture.state.persistedSessions.has(unknown), false, 'the fixture must not have created it');
      assert.ok(!rpcMethods(fixture).includes('session/selectModel'));
      assert.ok(!rpcMethods(fixture).includes('session/prompt'));
    } finally {
      await fixture.close();
    }
  });

  test('a persisted session is paged, adopted, and verified through workspace read-back', async () => {
    const sessionId = 'session-existing-42';
    const { fixture, host } = await connectFixture();
    try {
      const workspace = await resolveWorkspace(host, CWD, { timeoutMs: 5000 });
      const adopted = await adoptSession(host, { sessionId, workspaceId: workspace.id, cwd: CWD });

      assert.deepEqual(adopted, { id: workspace.id, path: CWD, sessionId, bound: true });
      assert.deepEqual(rpcMethods(fixture), ['workspace/create', 'session/page', 'session/create', 'workspace/create']);

      const page = fixture.rpcRequests('session/page')[0];
      assert.deepEqual(page.payload.address, { kind: 'session', sessionId });
      assert.equal(page.sentinel, true);
      assert.equal(page.persisted, true);

      const adoptions = fixture.rpcRequests('session/create');
      assert.equal(adoptions.length, 1);
      assert.deepEqual(adoptions[0].payload, { sessionId, workspaceId: workspace.id });
      assert.equal(adoptions[0].created, false, 'a persisted id is adopted, not created');

      const readBack = fixture.rpcRequests('workspace/create')[1];
      assert.equal(readBack.payload.path, CWD);
      assert.ok(fixture.state.workspaces.get(CWD).sessionIds.includes(sessionId), 'membership read-back lists the session');
      assert.deepEqual(fixture.state.sessionCreates, [{ sessionId, requestedSessionId: sessionId, created: false }]);
      assert.ok(!rpcMethods(fixture).includes('session/selectModel'));
      assert.ok(!rpcMethods(fixture).includes('session/prompt'));
    } finally {
      await fixture.close();
    }
  });

  test('the fixture persists the default ids and honors an explicit persistedSessions option', async () => {
    const standard = await startMockWeb();
    try {
      assert.ok(standard.state.persistedSessions.has('session-mock-0001'));
      assert.ok(standard.state.persistedSessions.has('session-existing-42'));
    } finally {
      await standard.close();
    }

    const custom = await startMockWeb({ persistedSessions: ['session-only-0009'] });
    try {
      assert.deepEqual([...custom.state.persistedSessions], ['session-only-0009']);
      const target = resolveWebTarget({ 'dsh-web-url': custom.launchUrl }, {});
      const host = await connectWebHost(target, { timeoutMs: 5000 });
      const workspace = await resolveWorkspace(host, CWD, { timeoutMs: 5000 });
      const adopted = await adoptSession(host, { sessionId: 'session-only-0009', workspaceId: workspace.id, cwd: CWD });
      assert.equal(adopted.bound, true);
      await assert.rejects(
        () => adoptSession(host, { sessionId: 'session-mock-0001', workspaceId: workspace.id, cwd: CWD }),
        (error) => {
          assert.equal(error.code, 'session/not-found', 'the default ids are replaced, not merged');
          return true;
        },
      );
      assert.equal(custom.rpcRequests('session/create').length, 1);
    } finally {
      await custom.close();
    }
  });

  test('a page response without the documented shape stops adoption before session/create', async () => {
    const { fixture, host } = await connectFixture({ sessionPageMode: 'malformed' });
    try {
      const workspace = await resolveWorkspace(host, CWD, { timeoutMs: 5000 });
      await assert.rejects(
        () => adoptSession(host, { sessionId: 'session-mock-0001', workspaceId: workspace.id, cwd: CWD }),
        (error) => {
          assert.ok(error instanceof WebRpcError);
          assert.equal(error.code, 'bad-response');
          assert.match(error.message, /session\/page did not return the expected read-only page shape/);
          return true;
        },
      );
      assert.equal(fixture.rpcRequests('session/create').length, 0);
    } finally {
      await fixture.close();
    }
  });

  test('a read-back workspace whose canonical path differs from --cwd is rejected', async () => {
    const { fixture, host } = await connectFixture({
      workspaces: [{ workspaceId: 'workspace-1', path: CWD }],
      workspacePathOverride: OTHER_PATH,
    });
    try {
      await assert.rejects(
        () => adoptSession(host, { sessionId: 'session-existing-42', workspaceId: 'workspace-1', cwd: CWD }),
        (error) => {
          assert.ok(error instanceof WebRpcError);
          assert.equal(error.code, 'path-mismatch');
          assert.match(error.message, /did not report the requested canonical --cwd/);
          assert.ok(!error.message.includes(OTHER_PATH), 'the fixed diagnostic must not echo the unexpected path');
          return true;
        },
      );
      assert.equal(fixture.rpcRequests('session/create').length, 1, 'the mismatch is caught on read-back');
    } finally {
      await fixture.close();
    }
  });
});
