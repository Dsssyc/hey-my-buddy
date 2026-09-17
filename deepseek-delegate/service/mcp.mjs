#!/usr/bin/env node
// Compatibility launcher; Python libraries and C-Two are managed solely by uv.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const project = fileURLToPath(new URL('../', import.meta.url));
const child = spawn(process.env.UV_BIN || 'uv', ['run', '--frozen', '--project', project, 'buddy-mcp'], { stdio: 'inherit' });
child.on('error', error => { console.error(error.message); process.exitCode = 1; });
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('exit', code => { process.exitCode = code ?? 1; });
