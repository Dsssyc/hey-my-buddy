#!/usr/bin/env node
/** Standard MCP stdio client. The launcher must supply the App's signed Node. */
import { spawn } from 'node:child_process';
import { isAbsolute } from 'node:path';

const MAX_BYTES = 8 * 1024 * 1024;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const override = Number(process.env.BUDDY_NATIVE_TIMEOUT_MS);
const timeoutMs = Number.isInteger(override) && override > 0 ? Math.min(override, 32000) : 32000;
let child;
let settled = false;
let finishing;
let closePromise;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function cleanup() {
  if (!child) return;
  child.stdin.end();
  await Promise.race([closePromise, pause(200)]);
  if (child.exitCode === null && child.signalCode === null) {
    child.kill('SIGTERM');
    await Promise.race([closePromise, pause(300)]);
  }
  if (child.exitCode === null && child.signalCode === null) {
    child.kill('SIGKILL');
    await closePromise;
  }
}

function finish(payload) {
  if (settled) return finishing;
  settled = true;
  clearTimeout(deadline);
  process.stdin.pause();
  finishing = cleanup().then(() => {
    process.stdout.write(JSON.stringify(payload) + '\n');
    process.exitCode = payload.error ? 1 : 0;
  });
  return finishing;
}
const fail = (code, message) => finish({ error: { code, message } });
const deadline = setTimeout(() => fail('TIMEOUT', 'Native MCP request timed out'), timeoutMs);
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => fail('INTERRUPTED', 'Native MCP request interrupted'));

function start(request) {
  const server = process.argv[2];
  if (!server || !isAbsolute(server)) return fail('INVALID_ARGUMENT', 'An absolute bundled server path is required');
  if (!request || typeof request !== 'object' || Array.isArray(request) || !['read_thread', 'send_message_to_thread'].includes(request.name)
      || !UUID.test(request.callerThread ?? '') || !request.arguments || typeof request.arguments !== 'object' || Array.isArray(request.arguments)
      || request.arguments.threadId !== request.callerThread) {
    return fail('INVALID_ARGUMENT', 'Only the bound caller task can be read or notified');
  }
  child = spawn(process.execPath, [server], { stdio: ['pipe', 'pipe', 'pipe'], env: process.env });
  closePromise = new Promise(resolve => child.once('close', resolve));
  child.on('error', () => fail('SPAWN_FAILED', 'Could not launch the bundled native MCP server'));
  child.stdin.on('error', () => { if (!settled) fail('TRANSPORT_ERROR', 'Native MCP input closed'); });
  // Never forward child logs: native diagnostics may contain private connection data.
  child.stderr.resume();
  const send = value => child.stdin.write(JSON.stringify({ jsonrpc: '2.0', ...value }) + '\n');
  let buffer = Buffer.alloc(0);
  let received = 0;
  let initialized = false;
  child.stdout.on('data', chunk => {
    if (settled) return;
    received += chunk.length;
    if (received > MAX_BYTES) return fail('RESPONSE_TOO_LARGE', 'Native MCP response exceeds 8 MiB');
    buffer = Buffer.concat([buffer, chunk]);
    let newline;
    while (!settled && (newline = buffer.indexOf(10)) !== -1) {
      const line = buffer.subarray(0, newline).toString('utf8');
      buffer = buffer.subarray(newline + 1);
      if (!line.trim()) continue;
      let message;
      try { message = JSON.parse(line); } catch { return fail('INVALID_RESPONSE', 'Native MCP returned invalid JSON'); }
      if (!message || message.jsonrpc !== '2.0') return fail('INVALID_RESPONSE', 'Native MCP returned an invalid envelope');
      if (message.method) {
        if (message.id !== undefined) send({ id: message.id, error: { code: -32601, message: 'This client does not handle server requests' } });
        continue;
      }
      if (message.id !== (initialized ? 2 : 1)) continue;
      if (message.error) return fail(message.error.code ?? 'MCP_ERROR', 'Native MCP request failed');
      if (!initialized) {
        if (!message.result || message.result.protocolVersion !== '2025-06-18') return fail('PROTOCOL_ERROR', 'Native MCP protocol negotiation failed');
        initialized = true;
        send({ method: 'notifications/initialized' });
        send({ id: 2, method: 'tools/call', params: { name: request.name, arguments: request.arguments, _meta: { 'openai/threadId': request.callerThread } } });
      } else {
        if (!message.result || !Array.isArray(message.result.content)) return fail('INVALID_RESPONSE', 'Native MCP returned an invalid tool result');
        finish({ result: message.result });
      }
    }
  });
  child.once('close', () => { if (!settled) fail('TRANSPORT_CLOSED', 'Native MCP server exited before returning a result'); });
  send({ id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'buddy-native-client', version: '0.3.0' } } });
}

let input = Buffer.alloc(0);
let started = false;
function consume() {
  if (started || settled) return;
  started = true;
  let request;
  try { request = JSON.parse(input.toString('utf8')); } catch { return fail('INVALID_ARGUMENT', 'Expected one JSON request'); }
  start(request);
}
process.stdin.on('data', chunk => {
  if (started || settled) return;
  if (input.length + chunk.length > MAX_BYTES) return fail('REQUEST_TOO_LARGE', 'Native MCP request exceeds 8 MiB');
  input = Buffer.concat([input, chunk]);
  const newline = input.indexOf(10);
  if (newline !== -1) { input = input.subarray(0, newline); consume(); }
});
process.stdin.on('end', consume);
process.stdin.on('error', () => fail('INPUT_ERROR', 'Could not read native MCP request'));
