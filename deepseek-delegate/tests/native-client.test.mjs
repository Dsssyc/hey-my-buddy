import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const helper = fileURLToPath(new URL('../service/native-client.mjs', import.meta.url));
const thread = '00000000-0000-4000-8000-000000000001';
function fixture(t, mode = 'normal') {
  const directory = mkdtempSync(join(tmpdir(), 'buddy-native-test-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const server = join(directory, 'server.mjs');
  writeFileSync(server, `import { createInterface } from 'node:readline';
const mode=${JSON.stringify(mode)}; let initialized=false;
const lines=createInterface({input:process.stdin});
lines.on('line',line=>{const m=JSON.parse(line);if(mode==='timeout')return;
if(m.method==='initialize')console.log(JSON.stringify({jsonrpc:'2.0',id:m.id,result:{protocolVersion:m.params.protocolVersion,capabilities:{tools:{}},serverInfo:{name:'mock',version:'1'}}}));
else if(m.method==='notifications/initialized')initialized=true;
else if(m.method==='tools/call'){
 if(mode==='oversized'){process.stdout.write('x'.repeat(8*1024*1024+1));return;}
 console.log(JSON.stringify({jsonrpc:'2.0',id:m.id,result:{content:[{type:'text',text:JSON.stringify({params:m.params,initialized,execPath:process.execPath})}]}}));
}});
lines.on('close',()=>process.exit(0));
`);
  return server;
}
function run(server, request, env = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [helper, server], { env: { ...process.env, ...env }, stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.on('data', chunk => stdout += chunk); child.stderr.on('data', chunk => stderr += chunk);
    child.on('error', reject);
    child.on('close', code => { try { resolve({ code, reply: JSON.parse(stdout), stderr }); } catch (error) { reject(error); } });
    child.stdin.end(JSON.stringify(request) + '\n');
  });
}

test('standard MCP handshake preserves caller metadata and uses the same Node executable', async t => {
  const server = fixture(t);
  for (const name of ['read_thread', 'send_message_to_thread']) {
    const args = { threadId: thread, ...(name === 'read_thread' ? { turnLimit: 1 } : { prompt: 'fixed completion notice' }) };
    const { code, reply, stderr } = await run(server, { name, arguments: args, callerThread: thread });
    assert.equal(code, 0); assert.equal(stderr, '');
    const payload = JSON.parse(reply.result.content[0].text);
    assert.deepEqual(payload.params, { name, arguments: args, _meta: { 'openai/threadId': thread } });
    assert.equal(payload.initialized, true); assert.equal(payload.execPath, process.execPath);
  }
});

test('arbitrary tools and cross-task calls fail before spawning a server', async () => {
  for (const request of [
    { name: 'delete_thread', arguments: { threadId: thread }, callerThread: thread },
    { name: 'read_thread', arguments: { threadId: '00000000-0000-4000-8000-000000000002' }, callerThread: thread },
    { name: 'send_message_to_thread', arguments: { threadId: thread }, callerThread: 'not-a-uuid' },
  ]) {
    const { code, reply } = await run('/not/a/real/server.mjs', request);
    assert.equal(code, 1); assert.equal(reply.error.code, 'INVALID_ARGUMENT');
  }
});

test('deadline and response size limit terminate the owned child', async t => {
  const request = { name: 'read_thread', arguments: { threadId: thread }, callerThread: thread };
  const started = Date.now();
  const timeout = await run(fixture(t, 'timeout'), request, { BUDDY_NATIVE_TIMEOUT_MS: '100' });
  assert.equal(timeout.reply.error.code, 'TIMEOUT'); assert.ok(Date.now() - started < 2500);
  const oversized = await run(fixture(t, 'oversized'), request);
  assert.equal(oversized.reply.error.code, 'RESPONSE_TOO_LARGE');
});
