// Real-browser regression over the synthetic preview only. No daily board or cookies.
// PLAYWRIGHT_MODULE=/abs/node_modules/playwright/index.mjs node tests/probes/console_layout_check.mjs http://127.0.0.1:PORT/ OUTPUT_DIR
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const base = new URL(process.argv[2]);
assert.equal(base.hostname, '127.0.0.1');
assert.notEqual(base.port, '49637', 'Never use the daily console');
const output = resolve(process.argv[3] || 'tmp/browser');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const measurements = [], errors = [];
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  // Enough realistic session rows to exercise settings overflow even on a tall
  // display. Only a synthetic response is extended, never a board response.
  await page.route('**/api/console', async route => {
    const response = await route.fetch();
    assert.equal(response.headers()['x-buddy-preview'], 'synthetic-fixture-data');
    const snapshot = await response.json();
    snapshot.consoleAccess = { requireLogin: true, revision: 2, sessions: Array.from({ length: 16 }, (_, i) => ({
      id: i.toString(16).padStart(24, '0'), lastSeen: 1_790_582_400 + i, current: i === 0,
    })) };
    await route.fulfill({ response, json: snapshot });
  });
  for (const [width, height] of [[1280, 720], [1440, 900], [390, 720]]) {
    await page.setViewportSize({ width, height });
    const response = await page.goto(new URL(`?layout=${width}#buddy`, base).href);
    assert.equal(response.headers()['x-buddy-preview'], 'synthetic-fixture-data');
    await page.getByRole('region', { name: '路由状态', exact: true }).getByRole('button', { name: '详情', exact: true }).click();
    for (const name of ['DSH', 'ZCode', 'Codex', 'Claude Code']) {
      await page.getByRole('button', { name: `${name} 检测详情`, exact: true }).click();
    }
    const buddy = page.locator('.buddy-page');
    await buddy.hover({ position: { x: 10, y: 10 } });
    await page.mouse.wheel(0, 3000);
    await page.waitForFunction(() => document.querySelector('.buddy-page').scrollTop > 0);
    const model = page.getByRole('button', { name: /deepseek-flash.*已启用/ });
    await model.click();
    const modelHeight = await page.locator('.buddy-page > .workspace-grid').evaluate(e => e.getBoundingClientRect().height);
    assert.ok(modelHeight >= 420, `model region collapsed at ${width}: ${modelHeight}`);
    await checkLayout('buddy', width, height);
    await page.screenshot({ path: `${output}/buddy-${width}.png` });
    await page.getByRole('link', { name: '设置', exact: true }).click();
    await page.getByRole('button', { name: '退出当前登录' }).waitFor();
    const settings = page.locator('.settings-page');
    await settings.hover({ position: { x: 10, y: 10 } });
    await page.mouse.wheel(0, 4000);
    await page.waitForFunction(() => document.querySelector('.settings-page').scrollTop > 0);
    const cards = await settings.locator(':scope > .panel').evaluateAll(elements => elements.map(e => ({
      client: e.clientHeight, scroll: e.scrollHeight, height: e.getBoundingClientRect().height,
    })));
    assert.ok(cards.every(card => card.scroll <= card.client + 1), `settings card clipped at ${width}: ${JSON.stringify(cards)}`);
    await checkLayout('settings', width, height);
    await page.screenshot({ path: `${output}/settings-${width}.png` });
  }
  async function checkLayout(view, width, height) {
    const facts = await page.evaluate(selector => {
      const e = document.querySelector(selector);
      return { clientWidth: e.clientWidth, scrollWidth: e.scrollWidth, clientHeight: e.clientHeight,
        scrollHeight: e.scrollHeight, scrollTop: e.scrollTop, overflowY: getComputedStyle(e).overflowY,
        documentWidth: document.documentElement.scrollWidth, viewportWidth: innerWidth };
    }, view === 'buddy' ? '.buddy-page' : '.settings-page');
    assert.equal(facts.overflowY, 'auto');
    assert.ok(facts.scrollTop > 0 && facts.scrollHeight > facts.clientHeight, `${view} did not scroll`);
    assert.ok(facts.scrollWidth <= facts.clientWidth + 1, `${view} overflowed horizontally`);
    assert.ok(facts.documentWidth <= facts.viewportWidth, 'document overflowed horizontally');
    measurements.push({ view, width, height, ...facts });
  }
  assert.deepEqual(errors, []);
  await writeFile(`${output}/layout.json`, JSON.stringify({ measurements, errors }, null, 2) + '\n');
  console.log(JSON.stringify({ passed: measurements.length, measurements, errors }));
} finally { await browser.close(); }
