// Real-browser routing evidence check against synthetic preview data only.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const base = new URL(process.argv[2]), output = resolve(process.argv[3] || 'tmp/browser');
assert.equal(base.hostname, '127.0.0.1'); assert.notEqual(base.port, '49637');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const errors = [];
const basis = { candidateCount: 1, excludedCount: 1, excludedProfiles: [{
  profileId: 'dsh:deepseek-official:deepseek-flash:off', adapter: 'dsh', provider: 'deepseek-official',
  model: 'deepseek-flash', effort: 'off', reason: '合成：提交时被用户排除', source: 'override',
}] };
const selected = { adapter: 'dsh', provider: 'deepseek-official', model: 'deepseek-flash', effort: 'max' };
const fields = { selectedProfile: selected, routingBasis: basis, constraints: { adapter: 'dsh' }, requiredCapabilities: ['execution:dsh'],
  fallback: null, routingMode: 'fast', requestedRoutingMode: 'fast',
  reason: '唯一合法候选，未调用 Router', source: 'single-candidate', status: 'completed',
  taskId: null, attemptId: null, generation: null };
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.route('**/api/command', async route => {
    const body = route.request().postDataJSON();
    if (!['selection_get', 'workflow_get'].includes(body.operation)) return route.continue();
    const response = await route.fetch();
    assert.equal(response.headers()['x-buddy-preview'], 'synthetic-fixture-data');
    const data = await response.json();
    if (body.operation === 'workflow_get' && data.result.routing?.decisionId) {
      Object.assign(data.result.routing, fields);
      data.result.executionConfiguration = selected;
    }
    if (body.operation === 'selection_get') {
      Object.assign(data.result.decision, fields, { routerCalled: false, runId: null,
        budget: null, usage: null, input: null, evidence: [],
        output: { programSelection: { preferences: [] } }, inputVerification: null, nativeIdentity: null, stopEvidence: null });
    }
    await route.fulfill({ response, json: data });
  });
  const response = await page.goto(new URL('#tasks', base).href);
  assert.equal(response.headers()['x-buddy-preview'], 'synthetic-fixture-data');
  await page.getByRole('region', { name: '工作目标列表', exact: true }).locator('[data-objective-id]').first().getByRole('button').click();
  await page.locator('button.tl-item.tl-label').first().dblclick();
  await page.getByRole('tab', { name: '路由依据', exact: true }).click({ timeout: 5000 }).catch(async error => {
    console.error((await page.locator('body').innerText()).slice(-8000)); throw error;
  });
  await page.getByText('程序直选（唯一合法候选，未调用 Router）', { exact: true }).waitFor();
  const audit = page.getByRole('region', { name: '决策依据详情' });
  await audit.getByText('所需能力：execution:dsh', { exact: true }).waitFor();
  await audit.getByText(/用户排除 1 个：.*deepseek-flash.*off.*提交时被用户排除/).waitFor();
  const mode = await audit.locator('dt').filter({ hasText: /^实际模式$/ }).evaluate(e => e.nextElementSibling.textContent);
  assert.equal(mode, '未调用 Router');
  const facts = await audit.innerText();
  assert.ok(facts.includes('硬约束：dsh'));
  assert.ok(facts.includes('冻结记录，不随当前配置变化'));
  await audit.getByText(/用户排除 1 个/).scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${output}/single-candidate-routing.png` });
  assert.deepEqual(errors, []);
  await writeFile(`${output}/routing.json`, JSON.stringify({ facts, errors }, null, 2) + '\n');
  console.log(JSON.stringify({ passed: true, facts, errors }));
} finally { await browser.close(); }
