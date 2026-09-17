export async function waitForChange(manager, params, signal) {
  const { runId, afterRevision, timeoutMs = 30000 } = params;
  if (!Number.isInteger(timeoutMs) || timeoutMs < 0 || timeoutMs > 30000 ||
      (afterRevision !== undefined && (!Number.isInteger(afterRevision) || afterRevision < 0))) {
    throw Object.assign(new Error('wait requires timeoutMs 0..30000 and an optional nonnegative afterRevision'), { code: 'INVALID_INPUT' });
  }
  const current = await manager.dispatch('status', { runId });
  if (current.resultAvailable || timeoutMs === 0 || (afterRevision !== undefined && current.revision > afterRevision)) return current;
  return new Promise((resolveWait, reject) => {
    const cleanup = () => { clearTimeout(timer); manager.off('change', changed); signal?.removeEventListener('abort', abort); };
    const finish = async () => { cleanup(); try { resolveWait(await manager.dispatch('status', { runId })); } catch (error) { reject(error); } };
    const changed = id => { if (id === runId) void finish(); };
    const abort = () => { cleanup(); reject(Object.assign(new Error('Wait cancelled'), { code: 'ABORTED' })); };
    const timer = setTimeout(finish, timeoutMs);
    manager.on('change', changed);
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) { abort(); return; }
    // Close the subscribe/read race without polling the process or model.
    manager.dispatch('status', { runId }).then(latest => {
      if (latest.revision !== current.revision || latest.resultAvailable) void finish();
    }, error => { cleanup(); reject(error); });
  });
}
