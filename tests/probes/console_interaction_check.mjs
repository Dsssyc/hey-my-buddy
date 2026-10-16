// Run like console_layout_check.mjs against the synthetic preview only.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const base = new URL(process.argv[2]), output = resolve(process.argv[3] || 'tmp/browser');
assert.equal(base.hostname, '127.0.0.1'); assert.notEqual(base.port, '49637');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const errors = [], commands = [], checks = [];
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  let access = { requireLogin: false, revision: 0, sessions: [] };
  const self = { id: 'a'.repeat(24), lastSeen: 1_790_582_400, current: true };
  const other = { id: 'b'.repeat(24), lastSeen: 1_790_582_401, current: false };
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname === '/api/command') {
      const body = request.postDataJSON();
      if (['console_access_set', 'console_session_revoke'].includes(body.operation)) {
        commands.push(body);
        if (body.operation === 'console_access_set') {
          assert.deepEqual(Object.keys(body.params), ['requireLogin']);
          access = { requireLogin: body.params.requireLogin, revision: access.revision + 1,
            sessions: body.params.requireLogin ? [self, other] : [] };
        } else access = { ...access, sessions: access.sessions.filter(s => s.id !== body.params.sessionId) };
        return route.fulfill({ json: { ok: true, result: access } });
      }
    }
    if (url.pathname !== '/api/console') return route.continue();
    const response = await route.fetch();
    assert.equal(response.headers()['x-hey-my-buddy-preview'], 'synthetic-fixture-data');
    const data = await response.json();
    data.consoleAccess = access;
    return route.fulfill({ response, json: data });
  });
  const response = await page.goto(new URL('#settings', base).href);
  assert.equal(response.headers()['x-hey-my-buddy-preview'], 'synthetic-fixture-data');
  const toggle = page.getByRole('switch', { name: '需要登录' });
  await toggle.click();
  await page.getByRole('button', { name: '退出当前登录' }).waitFor();
  await page.getByRole('button', { name: '撤销登录 bbbbbbbb' }).click();
  await page.getByRole('button', { name: '撤销登录 bbbbbbbb' }).waitFor({ state: 'detached' });
  await toggle.click();
  await page.getByRole('button', { name: '退出当前登录' }).waitFor({ state: 'detached' });
  checks.push('settings login toggle and named session revocation use current revision');
  await page.screenshot({ path: `${output}/access-settings.png` });
  assert.deepEqual(errors, []);
  await writeFile(`${output}/interactions.json`, JSON.stringify({ checks, commands, errors }, null, 2) + '\n');
  console.log(JSON.stringify({ checks, commands, errors }));
} finally { await browser.close(); }
