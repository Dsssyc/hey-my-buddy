#!/usr/bin/env node
// Internal child of the C-Two host. Third-party dependencies live in uv/Python.
import { createInterface } from 'node:readline';
import { resolve } from 'node:path';
import { JobManager } from './jobs.mjs';
import { waitForChange } from './wait.mjs';
import { startDashboard } from './dashboard.mjs';
import { inquireRun } from './inquiry.mjs';

const stateDir = resolve(process.env.BUDDY_STATE_DIR);
const maxConcurrent = Number(process.env.BUDDY_MAX_CONCURRENT || 1);
if (!Number.isInteger(maxConcurrent) || maxConcurrent < 1 || maxConcurrent > 8) throw new Error('BUDDY_MAX_CONCURRENT must be 1..8');
const manager = new JobManager({ stateDir, maxConcurrent });
// Each connected `wait` client subscribes to 'change' until its run changes.
manager.setMaxListeners(100);
let dashboard, stopping = false;
const write = value => process.stdout.write(JSON.stringify(value) + '\n');
await manager.init();
write({ event: 'ready', protocol: 2, pid: process.pid });
const reader = createInterface({ input: process.stdin });
async function stop() {
  if (stopping) return;
  stopping = true;
  await manager.shutdown();
  if (dashboard) await (await dashboard).close();
  reader.close();
  process.stdin.destroy();
}
reader.on('line', async line => {
  let id;
  try {
    if (Buffer.byteLength(line) > 8 * 1024 * 1024) throw new Error('Request exceeds limit');
    const message = JSON.parse(line); id = message.id;
    const { method, params = {} } = message;
    if (stopping) throw Object.assign(new Error('Service is stopping'), { code: 'STOPPING' });
    let result;
    if (method === 'health') result = { protocol: 2, pid: process.pid, maxConcurrent, stateDir, persistenceError: manager.persistenceFailure?.message ?? null };
    else if (method === 'wait') result = await waitForChange(manager, params);
    // `inquire` performs bounded socket I/O, so it runs OUTSIDE the manager's
    // serialized dispatch tail: a pending `await`/`wait` is never blocked by it.
    else if (method === 'inquire') result = await inquireRun(manager, params);
    else if (method === 'dashboard') { dashboard ??= startDashboard(manager); result = { url: (await dashboard).url, readOnly: true }; }
    else if (method === 'stop') { await stop(); result = { stopped: true }; }
    else result = await manager.dispatch(method, params);
    write({ id, result });
  } catch (error) { write({ id, error: { code: error.code || 'ENGINE_ERROR', message: error.message } }); }
});
reader.once('close', () => { if (!stopping) void stop(); });
process.once('SIGTERM', () => void stop());
process.once('SIGINT', () => void stop());
