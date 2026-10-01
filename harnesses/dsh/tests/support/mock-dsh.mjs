#!/usr/bin/env node
/*
 * Mock dsh for the deepseek-delegate test suite.
 *
 * Tests copy this file to a temporary directory under the name `dsh` (paths
 * with spaces included) so the CLI can be exercised end to end without an
 * installed DeepSeek Harness and without any model call. It uses only dynamic
 * imports so it behaves identically whether Node treats the extensionless copy
 * as CommonJS or ESM.
 *
 * Behavior is driven by MOCK_* environment variables and MOCK_MODE:
 *   ok             print MOCK_STDOUT (default "mock dsh completed") and exit 0
 *   big            print MOCK_BYTES bytes (default 200000) and exit 0
 *   sleep          outlive an immediate deadline, then print and exit 0
 *   nonzero        print MOCK_STDERR and exit MOCK_EXIT_CODE (default 3)
 *   hang           stay alive until signalled (default SIGTERM handling)
 *   ignore         ignore SIGTERM/SIGINT, with a grandchild that also ignores them
 *   exit0-on-term  exit 0 on SIGTERM, with a grandchild that ignores SIGTERM
 * Every mode records argv, cwd and the settings/patch files, so the runner's
 * private patch overlay can be exercised end to end without a model call.
 */
(async () => {
  const fs = await import('node:fs');
  const path = await import('node:path');
  const childProcess = await import('node:child_process');
  const { pathToFileURL } = await import('node:url');

  const artifactDir = process.env.MOCK_ARTIFACT_DIR;
  const record = (name, text) => {
    if (artifactDir === undefined || artifactDir === '') return;
    fs.mkdirSync(artifactDir, { recursive: true });
    fs.writeFileSync(path.join(artifactDir, name), text);
  };

  record('self.txt', fs.realpathSync(process.argv[1]));
  record('pid.txt', String(process.pid));
  record('dsh-home.txt', process.env.DSH_HOME || '');
  record('cwd.txt', process.cwd());
  record('web-env.txt', process.env.DSH_WEB_URL === undefined ? 'unset' : 'set');
  const argv = process.argv.slice(2);
  record('argv.json', JSON.stringify(argv));

  /** Exercise the production turn plugin with the ordered native events, without a model. */
  async function finishTurn(entry) {
    const mode = process.env.MOCK_TURN_MODE || 'valid';
    if (mode === 'missing') return;
    if (mode === 'malformed') {
      fs.writeFileSync(entry.config.outputFile, '{malformed', { mode: 0o600 });
      return;
    }
    const { apply } = await import(pathToFileURL(entry.name).href);
    const listeners = new Map();
    const on = (name, fn) => { listeners.set(name, [...listeners.get(name) || [], fn]); return () => {}; };
    const emit = (name, ...args) => { for (const fn of listeners.get(name) || []) fn(...args); };
    const definitions = new Map();
    const session = { id: process.env.MOCK_SESSION_ID || 'session-mock-0001', header: { cwd: process.cwd() }, seq: 0 };
    const agent = { id: session.id, session, status: 'running', ctx: {
      on,
      tools: { register(definition) { definitions.set(definition.name, definition); return () => {}; }, guard() { return () => {}; } },
      systemPrompt: { section(value) { record('turn-context.txt', value.text); }, getSectionOrder() { return 1000; } },
    } };
    const ctx = { on, agents: { get(id) { return id === agent.id ? agent : undefined; }, roots() { return [agent]; } } };
    apply(ctx, entry.config);
    const append = (type, data, extra = {}) => {
      const event = { type, data, seq: session.seq++, time: 1, ...extra };
      emit('session/event', session, event);
      return event;
    };
    const message = { role: 'user', id: 'input-1', source: { kind: 'user' }, content: [{ type: 'text', text: argv.at(-1) }] };
    emit('agent/inbox/inserted', { agent, message });
    append('turn/start', { turn: 1 });
    append('step/start', { turn: 1, step: 1 });
    append('user/message', message);
    const nested = mode === 'ptc';
    const call = append('tool/call', { turn: 1, step: 1, callId: 'call-1', name: nested ? 'run_code' : 'buddy_finish_turn', arguments: '{}' });
    const outerToken = Symbol();
    const exec = { agent, callId: nested ? 'call-1:ptc:1' : 'call-1', rootCallId: 'call-1', name: 'buddy_finish_turn', token: Symbol(), signal: new AbortController().signal, concludeTurn() {}, ...(nested ? { parent: outerToken } : {}) };
    const outcome = JSON.parse(process.env.MOCK_TURN_OUTCOME || '{"disposition":"completed","summary":"Mock structured turn completed.","remaining":[],"decisions":[],"artifacts":[],"request":null}');
    const value = await definitions.get('buddy_finish_turn').execute(outcome, exec);
    emit('tools/result', exec, { isError: false, value, concludesTurn: true });
    if (nested) {
      append('tool/ptc-dispatch', { rootCallId: 'call-1', parentCallId: 'call-1', subCallId: exec.callId, name: 'buddy_finish_turn', isError: false });
      emit('tools/result', { agent, callId: 'call-1', rootCallId: 'call-1', name: 'run_code', token: outerToken, signal: exec.signal }, { isError: false, value: {}, concludesTurn: true });
    }
    append('tool/result', { turn: 1, step: 1, message: { source: { kind: 'tool', callId: 'call-1' }, content: [{ type: 'tool-result', toolCallId: 'call-1', isError: false }] } }, { sourceEventSeqs: [call.seq] });
    append('step/end', { turn: 1, step: 1 });
    append('turn/end', { turn: 1, reason: { kind: 'completed' } });
    agent.status = 'idle';
    await Promise.all((listeners.get('session/flush') || []).map((fn) => Promise.resolve().then(() => fn(session))));
    if (mode === 'stale') {
      const result = JSON.parse(fs.readFileSync(entry.config.outputFile, 'utf8'));
      result.attemptId = 'another-attempt';
      fs.writeFileSync(entry.config.outputFile, JSON.stringify(result));
    }
    if (mode === 'nonprivate') fs.chmodSync(entry.config.outputFile, 0o644);
  }

  const patchIndex = argv.indexOf('--patch');
  if (patchIndex !== -1 && argv[patchIndex + 1] !== undefined) {
    const patchPath = argv[patchIndex + 1];
    try {
      const patchText = fs.readFileSync(patchPath, 'utf8');
      record('patch.json', patchText);
      const entries = JSON.parse(patchText);
      const settingsPath = entries[0].config.path;
      record('settings-copy.json', fs.readFileSync(settingsPath, 'utf8'));
      record('settings-copy-dir.txt', path.dirname(settingsPath));
      const turnEntry = entries.flatMap((entry) => entry.insert || []).find((entry) => entry.id === 'deepseek-delegate-turn-result');
      if (turnEntry) await finishTurn(turnEntry);
    } catch (error) {
      record('patch-error.txt', String((error && error.message) || error));
    }
  }

  const mode = process.env.MOCK_MODE || 'ok';
  if (mode === 'big') {
    process.stdout.write('B'.repeat(Number(process.env.MOCK_BYTES || 200000)));
    process.exit(0);
  }
  if (mode === 'nonzero') {
    process.stderr.write(process.env.MOCK_STDERR || 'mock dsh failed\n');
    process.exit(Number(process.env.MOCK_EXIT_CODE || 3));
  }
  if (mode === 'sleep') {
    // A real delay a wrong immediate deadline could not survive.
    await new Promise((resolve) => setTimeout(resolve, Number(process.env.MOCK_SLEEP_MS || 1500)));
    process.stdout.write(process.env.MOCK_STDOUT || 'mock dsh completed\n');
    process.exit(0);
  }
  if (mode === 'hang' || mode === 'ignore' || mode === 'exit0-on-term' || mode === 'orphan') {
    // Install handlers before recording pids so a test that reacts to the
    // recorded file never races an unarmed mock.
    if (mode === 'ignore') {
      process.on('SIGTERM', () => {});
      process.on('SIGINT', () => {});
    } else if (mode === 'exit0-on-term') {
      process.on('SIGTERM', () => setTimeout(() => process.exit(0), 50));
      process.on('SIGINT', () => setTimeout(() => process.exit(0), 50));
    }
    let grandchild = null;
    if (mode !== 'hang') {
      grandchild = childProcess.spawn(
        process.execPath,
        ['-e', 'process.on("SIGTERM",()=>{});process.on("SIGINT",()=>{});setInterval(()=>{},1000);process.send("ready");'],
        { stdio: ['ignore', 'ignore', 'ignore', 'ipc'] },
      );
      // Publish the PID only after ignoring signals is active, so cancellation
      // tests exercise actual descendant cleanup rather than a startup race.
      await new Promise((resolveReady, rejectReady) => {
        const timer = setTimeout(() => {
          grandchild.kill('SIGKILL');
          rejectReady(new Error('mock grandchild did not become ready'));
        }, 5000);
        grandchild.once('message', () => { clearTimeout(timer); resolveReady(); });
        grandchild.once('error', (error) => { clearTimeout(timer); rejectReady(error); });
      });
    }
    record('pids.json', JSON.stringify({ self: process.pid, grandchild: grandchild === null ? null : grandchild.pid }));
    if (mode === 'orphan') process.exit(0);
    setInterval(() => {}, 1000);
    return;
  }
  process.stdout.write(process.env.MOCK_STDOUT || 'mock dsh completed\n');
  process.exit(0);
})();
