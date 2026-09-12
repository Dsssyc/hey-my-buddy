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
 *   nonzero        print MOCK_STDERR and exit MOCK_EXIT_CODE (default 3)
 *   hang           stay alive until signalled (default SIGTERM handling)
 *   ignore         ignore SIGTERM/SIGINT, with a grandchild that also ignores them
 *   exit0-on-term  exit 0 on SIGTERM, with a grandchild that ignores SIGTERM
 * Every mode records argv and the settings/patch files under MOCK_ARTIFACT_DIR.
 */
(async () => {
  const fs = await import('node:fs');
  const path = await import('node:path');
  const childProcess = await import('node:child_process');

  const artifactDir = process.env.MOCK_ARTIFACT_DIR;
  const record = (name, text) => {
    if (artifactDir === undefined || artifactDir === '') return;
    fs.mkdirSync(artifactDir, { recursive: true });
    fs.writeFileSync(path.join(artifactDir, name), text);
  };

  record('self.txt', fs.realpathSync(process.argv[1]));
  record('dsh-home.txt', process.env.DSH_HOME || '');
  const argv = process.argv.slice(2);
  record('argv.json', JSON.stringify(argv));

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
  if (mode === 'hang' || mode === 'ignore' || mode === 'exit0-on-term') {
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
    setInterval(() => {}, 1000);
    return;
  }
  process.stdout.write(process.env.MOCK_STDOUT || 'mock dsh completed\n');
  process.exit(0);
})();
