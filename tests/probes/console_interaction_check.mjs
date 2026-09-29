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
  let updated = 0;
  let access = { requireLogin: false, revision: 0, sessions: [] };
  const self = { id: 'a'.repeat(24), lastSeen: 1_790_582_400, current: true };
  const other = { id: 'b'.repeat(24), lastSeen: 1_790_582_401, current: false };
  let rows;
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname === '/api/command') {
      const body = request.postDataJSON();
      if (['console_access_set', 'console_session_revoke'].includes(body.operation)) {
        commands.push(body);
        if (body.operation === 'console_access_set') {
          assert.equal(body.params.expectedRevision, access.revision);
          access = { requireLogin: body.params.requireLogin, revision: access.revision + 1,
            sessions: body.params.requireLogin ? [self, other] : [] };
        } else access = { ...access, sessions: access.sessions.filter(s => s.id !== body.params.sessionId) };
        return route.fulfill({ json: { ok: true, result: access } });
      }
    }
    if (!['/api/console', '/api/objectives'].includes(url.pathname)) return route.continue();
    const response = await route.fetch();
    assert.equal(response.headers()['x-buddy-preview'], 'synthetic-fixture-data');
    const data = await response.json();
    if (url.pathname === '/api/console') data.consoleAccess = access;
    else {
      const original = data.objectives.find(row => row.kind === 'objective');
      rows = Array.from({ length: 24 }, (_, i) => ({ ...original,
        objectiveId: i === 0 ? original.objectiveId : `obj-90000000-0000-4000-8000-${String(i).padStart(12, '0')}`,
        title: i === 0 ? original.title : `合成目标 ${i}`,
        lastActivitySeq: i === updated && updated ? 1000 + updated : 100 - i,
      }));
      data.objectives = [...rows].sort((a, b) => b.lastActivitySeq - a.lastActivitySeq);
      data.total = rows.length; data.nextCursor = null; data.changed = false;
    }
    return route.fulfill({ response, json: data });
  });
  const response = await page.goto(new URL('#tasks', base).href);
  assert.equal(response.headers()['x-buddy-preview'], 'synthetic-fixture-data');
  const list = page.getByRole('region', { name: '工作目标列表', exact: true });
  await list.locator('[data-objective-id]').first().waitFor();
  const firstId = () => list.locator('[data-objective-id]').first().getAttribute('data-objective-id');
  const waitFirst = index => page.waitForFunction(id => document.querySelector('.list-panel [data-objective-id]')?.getAttribute('data-objective-id') === id, rows[index].objectiveId);
  assert.equal(await firstId(), rows[0].objectiveId);
  updated = 1;
  await page.getByRole('button', { name: '刷新工作台' }).click();
  await waitFirst(1);
  assert.equal(await list.getByRole('button', { name: '有更新' }).count(), 0);
  checks.push('top and pointer outside: silent reorder');
  await list.locator(`[data-objective-id="${rows[0].objectiveId}"] button`).click();
  await page.getByRole('complementary', { name: '工作目标详情' }).waitFor();
  await list.hover({ position: { x: 20, y: 20 } });
  updated = 2;
  const marker = list.getByRole('button', { name: '有更新' });
  await marker.waitFor();
  assert.equal(await firstId(), rows[1].objectiveId);
  assert.equal(await list.locator('.new-records').count(), 0);
  await marker.click();
  await waitFirst(2);
  assert.equal(await list.locator(`[data-objective-id="${rows[0].objectiveId}"] button`).getAttribute('aria-pressed'), 'true');
  checks.push('hover defers; heading marker applies and preserves selection');
  const scroll = list.getByLabel('工作目标条目', { exact: true });
  await scroll.hover(); await page.mouse.wheel(0, 350);
  await page.waitForFunction(() => document.querySelector('[aria-label="工作目标条目"]').scrollTop > 0);
  updated = 3;
  await page.getByRole('button', { name: '刷新工作台' }).click();
  await marker.waitFor();
  assert.equal(await firstId(), rows[2].objectiveId);
  await scroll.press('Home');
  await waitFirst(3);
  checks.push('scrolled list defers; returning to top applies');
  await page.screenshot({ path: `${output}/objective-updates.png` });
  await page.getByRole('link', { name: '设置', exact: true }).click();
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
