/**
 * deepseek-delegate session-capture observer.
 *
 * A tiny, self-contained Cordis plugin that the delegation CLI mounts into a
 * headless dsh run through a temporary `--patch` overlay. Its only job is to
 * write down the EXACT session id of this run's root session, privately, so the
 * CLI can group that session in the owning workspace host
 * after the headless process has stopped.
 *
 * Why an observer instead of reading storage: `dsh-storage-json` documents that
 * its registry/cache/feed files are host-local with no cross-process locking,
 * so a second process must never write them. This adapter uses the owning
 * host's official workspace API through its private socket, and this plugin only supplies the identity the host needs.
 *
 * Correlation contract (verified against the installed dsh sources):
 * - `session/event` is emitted as `(session, event)`; for a `user/message`
 *   event, `event.data` is the UserMessage itself, e.g.
 *   `{ id, role: 'user', content: [{ type: 'text', text }], source: { kind: 'user' } }`.
 * - A root session has no `header.parentSession`, no `'subagent'` origin, and no
 *   positive `header.delegationDepth`.
 * - Headless creates its one session with `meta: { cwd: process.cwd() }`, so
 *   `header.cwd` is the canonical `--cwd` the CLI spawned it in.
 * The plugin therefore matches the root session whose first ordinary user
 * message text hashes to the exact prompt this CLI delivered. It never guesses
 * from timestamps, newest files, or "the first session seen".
 *
 * This module uses only node builtins and the normal injected Cordis context
 * (`ctx.on`); it must never throw into the run, never mutate events, and never
 * delay task behavior.
 *
 * @module deepseek-delegate/plugins/session-capture
 */
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';

/** Stable Cordis plugin name (the patch entry id mirrors it). */
export const name = 'deepseek-delegate-session-capture';

/** Capture metadata format version written to disk. */
const CAPTURE_VERSION = 1;

/** Lowercase hex SHA-256 of one UTF-8 string. */
function sha256(text) {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

/** Join the text blocks of a message the way the CLI hashes the delivered prompt. */
function messageText(message) {
  if (message === null || typeof message !== 'object') return undefined;
  const content = message.content;
  if (!Array.isArray(content)) return undefined;
  let text = '';
  for (const block of content) {
    if (block === null || typeof block !== 'object' || block.type !== 'text') continue;
    if (typeof block.text !== 'string') return undefined;
    text += block.text;
  }
  return text;
}

/** Root lineage: no fork parent, no subagent origin, no positive delegation depth. */
function isRootSession(session) {
  const header = session?.header;
  if (header === null || typeof header !== 'object') return false;
  if (header.parentSession !== undefined && header.parentSession !== null) return false;
  if (header.origin === 'subagent') return false;
  if (typeof header.delegationDepth === 'number' && header.delegationDepth > 0) return false;
  return true;
}

/**
 * Mount the observer. `config` is plain patch data (the plugin exports no
 * schema), and every field is optional so a malformed patch can never break
 * boot: missing or invalid configuration simply disables capture.
 *
 * @param ctx - Cordis context carrying the session event feed.
 * @param config - `{ capturePath, promptSha256, cwd }` written by the CLI patch.
 */
export function apply(ctx, config) {
  const capturePath = config?.capturePath;
  const promptSha256 = config?.promptSha256;
  const cwd = config?.cwd;
  if (typeof capturePath !== 'string' || capturePath === '') return;
  if (typeof promptSha256 !== 'string' || !/^[0-9a-f]{64}$/.test(promptSha256)) return;
  if (typeof cwd !== 'string' || cwd === '') return;

  let capturedId;
  let ambiguous = false;
  ctx.on('session/event', (session, event) => {
    if (ambiguous) return;
    try {
      if (!isRootSession(session)) return;
      if (session.header.cwd !== cwd) return;
      if (event?.type !== 'user/message') return;
      if (event.data?.source?.kind !== 'user') return;
      const text = messageText(event.data);
      if (text === undefined || sha256(text) !== promptSha256) return;
      if (capturedId === String(session.id)) return;
      if (capturedId !== undefined) {
        writeFileSync(capturePath, `${JSON.stringify({ version: CAPTURE_VERSION, cwd, promptSha256, ambiguous: true })}\n`, { mode: 0o600 });
        ambiguous = true;
        return;
      }
      const record = {
        version: CAPTURE_VERSION,
        sessionId: String(session.id),
        cwd: session.header.cwd,
        promptSha256,
        seq: typeof event.seq === 'number' ? event.seq : null,
        capturedAt: new Date().toISOString(),
      };
      writeFileSync(capturePath, `${JSON.stringify(record, null, 2)}\n`, { mode: 0o600, flag: 'wx' });
      capturedId = record.sessionId;
    } catch {
      // An observer must never affect the delegated run: a failed write just
      // leaves the capture missing, which the CLI reports and exits nonzero.
    }
  });
}
