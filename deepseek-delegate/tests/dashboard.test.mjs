import test from 'node:test';
import assert from 'node:assert/strict';
import { request } from 'node:http';
import { runInNewContext } from 'node:vm';
import { startDashboard } from '../service/dashboard.mjs';

const runId = '00000000-0000-4000-8000-000000000001';
const attack = '</script><img src=x onerror="alert(1)">';
function get(url, options = {}) {
  return new Promise((resolve, reject) => {
    const req = request(url, options, res => {
      let body = ''; res.setEncoding('utf8'); res.on('data', chunk => body += chunk);
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body }));
    }); req.on('error', reject); req.end();
  });
}
async function fixture(t) {
  const calls = [];
  const run = { runId, cwd: attack, status: 'completed', createdAt: '2026-09-15', acceptedAt: null, resultAvailable: true, logPaths: { stdout: '/private/runner.log' } };
  const manager = { dispatch: async (method, params) => {
    calls.push([method, params]);
    if (method === 'list') return { runs: [run], total: 1 };
    if (params.runId !== runId) throw Object.assign(new Error(), { code: 'NOT_FOUND' });
    return method === 'result' ? { ...run, result: { finalText: attack } } : run;
  } };
  const dashboard = await startDashboard(manager); t.after(() => dashboard.close());
  return { ...dashboard, calls };
}

test('dashboard only allows GET with its token and loopback Host / matching Origin', async t => {
  const { url, calls } = await fixture(t);
  const origin = new URL(url).origin;
  for (const method of ['POST', 'PUT', 'DELETE', 'HEAD']) assert.equal((await get(url, { method })).status, 405);
  assert.equal((await get(url, { headers: { Host: 'evil.example' } })).status, 403);
  assert.equal((await get(url, { headers: { Origin: 'https://evil.example' } })).status, 403);
  assert.equal((await get(url, { headers: { Origin: 'null' } })).status, 403);
  assert.equal((await get(origin + '/wrong/api')).status, 404);
  assert.equal((await get(url + 'api/../../etc/passwd')).status, 404);
  assert.equal((await get(url + 'api/' + runId + '/cancel')).status, 404);
  assert.equal(calls.length, 0);
  assert.equal((await get(url, { headers: { Origin: origin } })).status, 200);
});

test('API reads list, status and result only and preserves untrusted strings as JSON', async t => {
  const { url, calls } = await fixture(t);
  const list = await get(url + 'api'); assert.equal(list.status, 200);
  assert.equal(JSON.parse(list.body).runs[0].cwd, attack);
  const result = await get(url + 'api/' + runId);
  assert.equal(JSON.parse(result.body).result.finalText, attack);
  assert.deepEqual(calls.map(([method]) => method), ['list', 'status', 'result']);
  assert.deepEqual(calls[0][1], { limit: 100, offset: 0 });
  assert.equal(result.headers['content-type'], 'application/json; charset=utf-8');
  assert.equal((await get(url + 'api/00000000-0000-4000-8000-000000000002')).status, 404);
});

test('page CSP and DOM rendering keep task HTML inert', async t => {
  const { url } = await fixture(t);
  const html = await get(url);
  assert.equal(html.body.includes(attack), false);
  const [, nonce, script] = html.body.match(/<script nonce="([^"]+)">([\s\S]*?)<\/script>/);
  assert.ok(html.headers['content-security-policy'].includes("script-src 'nonce-" + nonce + "'"));
  assert.ok(html.headers['content-security-policy'].includes("frame-ancestors 'none'"));
  assert.equal(html.headers['referrer-policy'], 'no-referrer');
  const elements = new Map();
  const node = () => ({ textContent: '', dataset: {}, focus() { document.activeElement = this; }, replaceChildren(...children) { this.children = children; }, set innerHTML(_) { throw new Error('Unsafe HTML assignment'); } });
  const document = { getElementById(id) { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); }, createElement: node };
  let timer;
  runInNewContext(script, { document, location: new URL(url), fetch: async address => ({ ok: true, json: async () => JSON.parse((await get(address.startsWith('/') ? new URL(url).origin + address : address)).body) }), setTimeout(fn, delay) { assert.equal(delay, 3000); timer = fn; } });
  for (let i = 0; i < 100 && !timer; i++) await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(typeof timer, 'function');
  const button = elements.get('runs').children[0]; assert.ok(button.textContent.includes(attack));
  button.onclick();
  for (let i = 0; i < 100 && !elements.get('detail')?.textContent; i++) await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(elements.get('detail').textContent, attack);
  assert.ok(elements.get('raw-detail').textContent.includes(attack.replaceAll('"', '\\"')));
  assert.ok(html.body.includes('<details><summary>日志路径与完整记录</summary>'));
  button.focus();
  await timer();
  assert.notEqual(document.activeElement, button);
  assert.equal(document.activeElement.dataset.runId, runId);
});

test('empty list displays a clear empty state', async t => {
  const dashboard = await startDashboard({ dispatch: async () => ({ runs: [], total: 0 }) });
  t.after(() => dashboard.close());
  const html = await get(dashboard.url);
  const script = html.body.match(/<script nonce="[^"]+">([\s\S]*?)<\/script>/)[1];
  const elements = new Map();
  const node = () => ({ dataset: {}, replaceChildren(...children) { this.children = children; } });
  const document = { getElementById(id) { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); }, createElement: node };
  let ready;
  const refreshed = new Promise(resolve => ready = resolve);
  runInNewContext(script, { document, location: new URL(dashboard.url), fetch: async () => ({ ok: true, json: async () => ({ runs: [], total: 0 }) }), setTimeout: ready });
  await refreshed;
  assert.equal(elements.get('runs').children[0].textContent, '还没有委派任务');
});
