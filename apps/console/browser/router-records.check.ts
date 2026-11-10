import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { expect, type Locator, type Page } from "@playwright/test";
import { open, settle, test } from "./preview";

const captures = fileURLToPath(new URL("../../../tmp/router-ui/", import.meta.url));
const macro = "macro-11111111-1111-4111-8111-111111111111";
const instant = (index: number) => new Date(Date.UTC(2026, 9, 1, 0, index)).toISOString();

async function capture(page: Page, name: string) {
  await mkdir(captures, { recursive: true });
  await settle(page);
  await page.screenshot({ path: join(captures, name) });
}

/** Keep the server synthetic; only expand its in-memory response for long histories. */
async function longRecords(page: Page) {
  await page.route("**/api/console", async route => {
    const response = await route.fetch();
    const snapshot = await response.json();
    const maintenance = snapshot.maintenance.runs[0];
    snapshot.maintenance.runs = Array.from({ length: 44 }, (_, index) => ({
      ...maintenance, maintenanceRunId: `synthetic-maintenance-${index}`, taskId: "preview-run-a3",
      startedAt: instant(index === 0 ? 60 : index), finishedAt: index === 0 ? null : instant(index + 1),
      state: index === 0 ? "running" : "completed", triggerType: index === 0 ? "manual" : "interval",
    }));
    const failure = snapshot.routingHealth.recentFailures[0];
    snapshot.routingHealth.recentFailures = Array.from({ length: 30 }, (_, index) => ({
      ...failure, decisionId: `synthetic-failure-${index}`, at: instant(index), title: `合成失败 ${index}`,
    }));
    const model = snapshot.models.find((row: { latestProfileRun: unknown }) => row.latestProfileRun);
    snapshot.models = Array.from({ length: 20 }, (_, index) => ({
      ...model, modelId: `synthetic-profile-${index}`, displayName: `synthetic-model-${index}`,
      latestProfileRun: { ...model.latestProfileRun, profileRunId: `synthetic-profile-run-${index}`,
        startedAt: instant(index), finishedAt: instant(index + 1), state: "completed", reason: null },
    }));
    await route.fulfill({ response, json: snapshot });
  });
}

async function scrollHistory(page: Page, region: Locator, count: number) {
  await region.scrollIntoViewIfNeeded();
  const viewport = region.locator(':scope > [data-slot="scroll-area-viewport"]');
  await expect(viewport).toHaveAttribute("tabindex", "0");
  await expect(region.locator("table > tbody > tr")).toHaveCount(count);
  const before = await viewport.evaluate(node => ({ height: node.clientHeight, scroll: node.scrollHeight,
    header: node.querySelector("th")!.getBoundingClientRect().top }));
  expect(before.height).toBeLessThanOrEqual(308);
  expect(before.scroll).toBeGreaterThan(before.height * 2);
  await viewport.focus();
  await page.keyboard.press("PageDown");
  await expect.poll(() => viewport.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  expect(Math.abs(await region.locator("th").first().evaluate(node => node.getBoundingClientRect().top) - before.header)).toBeLessThanOrEqual(1);
  return before;
}

async function keyboardToBottom(page: Page, region: Locator) {
  const viewport = region.locator(':scope > [data-slot="scroll-area-viewport"]');
  await viewport.focus();
  for (let pageDown = 0; pageDown < 20; pageDown++) await page.keyboard.press("PageDown");
  await expect.poll(() => viewport.evaluate(node => Math.abs(node.scrollHeight - node.clientHeight - node.scrollTop))).toBeLessThanOrEqual(1);
}

async function expectNarrowTable(page: Page, region: Locator) {
  const geometry = await region.evaluate(node => {
    const viewport = node.querySelector(':scope > [data-slot="scroll-area-viewport"]')!;
    const table = viewport.querySelector("table")!;
    const cells = [...table.querySelectorAll("tbody > tr:first-child > td")].map(cell => cell.getBoundingClientRect());
    const times = [...table.querySelectorAll("tbody time")].map(time => {
      const range = document.createRange(); range.selectNodeContents(time);
      const rects = [...range.getClientRects()].filter(rect => rect.width > 0);
      const cell = time.closest("td")!.getBoundingClientRect();
      return { lines: new Set(rects.map(rect => rect.top)).size,
        inside: rects.every(rect => rect.left >= cell.left - 1 && rect.right <= cell.right + 1) };
    });
    const frame = node.getBoundingClientRect();
    return { times, frameRight: frame.right, viewport: document.documentElement.clientWidth,
      visibleWidth: viewport.clientWidth, tableWidth: table.getBoundingClientRect().width,
      gaps: cells.slice(1).map((cell, index) => cell.left - cells[index].right) };
  });
  expect(geometry.frameRight).toBeLessThanOrEqual(geometry.viewport + 1);
  expect(geometry.tableWidth).toBeGreaterThan(geometry.visibleWidth);
  for (const gap of geometry.gaps) expect(gap).toBeGreaterThanOrEqual(-1);
  expect(geometry.times.length).toBeGreaterThan(0);
  for (const time of geometry.times) {
    expect(time.lines, "timestamps stay on one line").toBe(1);
    expect(time.inside, "timestamps stay inside their own column").toBe(true);
  }
  const viewport = region.locator(':scope > [data-slot="scroll-area-viewport"]');
  await viewport.evaluate(node => { node.scrollLeft = node.scrollWidth; });
  await expect.poll(() => viewport.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
}

async function expectIdentifierTarget(target: Locator) {
  const hit = await target.evaluate(node => {
    const box = node.getBoundingClientRect(), x = box.left + box.width / 2;
    return { width: box.width, height: box.height,
      edges: [box.top + 1, box.bottom - 1].map(y => node.contains(document.elementFromPoint(x, y))) };
  });
  expect(hit.height).toBeGreaterThanOrEqual(24);
  expect(hit.width).toBeGreaterThanOrEqual(24);
  expect(hit.edges).toEqual([true, true]);
}

for (const theme of ["dark", "light"]) test.describe(`Router bounded records ${theme}`, () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
  });

  test("keeps all histories scrollable, headers fixed and active maintenance pinned", async ({ page }) => {
    await longRecords(page);
    await open(page, "#buddies/router/maintenance");
    const region = page.locator('[data-slot="maintenance-records-region"]');
    await region.scrollIntoViewIfNeeded();
    const running = region.locator("tbody > tr").first();
    await expect(running.locator('[aria-label="进行中"]')).toHaveCount(1);
    await expect(running.getByRole("button", { name: "停止", exact: true })).toBeVisible();
    await expect(running.locator("time").first()).toHaveAttribute("datetime", instant(60));
    await expect(region.locator("tbody > tr").nth(1).locator("time").first()).toHaveAttribute("datetime", instant(43));
    await capture(page, `maintenance-top-${theme}-1440.png`);
    const bounds = await running.boundingBox();
    await scrollHistory(page, region, 44);
    expect(Math.abs((await running.boundingBox())!.y - bounds!.y)).toBeLessThanOrEqual(1);
    await region.locator(':scope > [data-slot="scroll-area-viewport"]').evaluate(node => { node.scrollTop = (node.scrollHeight - node.clientHeight) / 2; });
    await capture(page, `maintenance-middle-${theme}-1440.png`);
    await keyboardToBottom(page, region);
    await expect(region.locator("tbody > tr").last().locator("time").first()).toHaveAttribute("datetime", instant(1));

    await page.getByRole("navigation", { name: "Router 的工作" }).getByRole("link", { name: /^路由/ }).click();
    const failures = page.locator('[data-slot="routing-failures-region"]');
    await scrollHistory(page, failures, 30);
    await expect(failures.locator("tbody > tr").first()).toContainText("合成失败 29");
    await keyboardToBottom(page, failures);
    await expect(failures.locator("tbody > tr").last().getByRole("link")).toHaveAttribute("href", /preview-run-a2$/);
    await failures.locator(':scope > [data-slot="scroll-area-viewport"]').evaluate(node => { node.scrollTop = 0; });
    await capture(page, `routing-records-${theme}-1440.png`);

    const nav = page.getByRole("navigation", { name: "Router 的工作" });
    await nav.getByRole("link", { name: /^画像/ }).click();
    const profiles = page.locator('[data-slot="profile-records"]');
    await scrollHistory(page, profiles, 20);
    await expect(profiles.locator("tbody > tr").first()).toContainText("synthetic-model-19");
    await keyboardToBottom(page, profiles);
    await expect(profiles.locator("tbody > tr").last()).toContainText("synthetic-model-0");
    await profiles.locator("tbody > tr").last().getByRole("button", { name: "synthetic-model-0", exact: true }).click();
    await expect(profiles.locator("table").first().locator(":scope > tbody > tr").last().getByRole("table")).toHaveCount(2);
    await profiles.locator(':scope > [data-slot="scroll-area-viewport"]').evaluate(node => { node.scrollTop = 0; });
    await capture(page, `profile-records-${theme}-1440.png`);
  });

  test("keeps record columns apart inside scrollable regions at 390px", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await longRecords(page);
    await open(page, "#buddies/router/maintenance");
    await expectNarrowTable(page, page.locator('[data-slot="maintenance-records-region"]'));

    const nav = page.getByRole("navigation", { name: "Router 的工作" });
    await nav.getByRole("link", { name: /^画像/ }).click();
    await expectNarrowTable(page, page.locator('[data-slot="profile-records"]'));

    await nav.getByRole("link", { name: /^路由/ }).click();
    await expectNarrowTable(page, page.locator('[data-slot="routing-routers-region"]'));
    await expectNarrowTable(page, page.locator('[data-slot="routing-failures-region"]'));
  });

  test("offers a full page refresh after a changed runtime", async ({ page }) => {
    let runtime = "synthetic-runtime-before";
    await page.route("**/api/console", async route => {
      const response = await route.fetch(); const snapshot = await response.json();
      snapshot.runtimeIdentity = runtime;
      await route.fulfill({ response, json: snapshot });
    });
    await open(page, "#buddies/router/routing");
    const notice = page.getByRole("status").filter({ hasText: "控制台已更新，刷新后使用新版本" });
    await expect(notice).toHaveCount(0);
    runtime = "synthetic-runtime-after";
    await expect(notice).toBeVisible({ timeout: 10000 });
    await capture(page, `refresh-notice-${theme}-1440.png`);
    await Promise.all([page.waitForEvent("load"), notice.getByRole("button", { name: "刷新", exact: true }).click()]);
    await expect(page.getByRole("navigation", { name: "Router 的工作" })).toBeVisible();
    await expect(notice).toHaveCount(0);
  });

  test("keeps all identifier targets at least 24px without growing their fact rows", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    const identifier = page.locator('[data-slot="macro-overview-facts"] [data-slot="copyable-identifier"]');
    await expect(identifier).toBeVisible();
    await expectIdentifierTarget(identifier);
    expect(await identifier.evaluate(node => node.closest("dd")!.parentElement!.getBoundingClientRect().height)).toBe(20);
    await identifier.hover();
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await capture(page, `copy-hover-${theme}-1440.png`);
    await page.route("**/api/command", async route => {
      const response = await route.fetch(); const data = await response.json();
      if (route.request().postDataJSON().operation === "workflow_get") {
        for (const turn of data.result.turns) turn.sessionId = "synthetic-session-11111111-1111-4111-8111-111111111111";
      }
      await route.fulfill({ response, json: data });
    });
    await page.goto(`#tasks/${macro}/preview-run-a1/3`);
    const ids = page.locator('[data-slot="detail-header"] [data-slot="copyable-identifier"], [data-slot="detail-band"] [data-slot="copyable-identifier"]');
    await expect(ids).toHaveCount(2);
    for (const target of await ids.all()) await expectIdentifierTarget(target);
  });
});
