import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { expectNoSidewaysScroll, open, settle, test } from "./preview";

const ROUTING = "preview-run-a1-router";
const COMMAND = "preview-command-c1";
const MICRO = "preview-run-a1";
const HELPER = "preview-run-b1-h1";
const words = [
  { locale: "zh-CN", today: "今天", yesterday: "昨天", search: "搜索标题、项目、Host 或编号", noMatch: "没有匹配的记录", empty: "还没有记录", records: "记录", macro: "宏任务", failed: "读取失败", helper: "协助任务", calculation: "路由计算", command: "命令执行", input: "输入", recordDetail: "记录详情" },
  { locale: "en-US", today: "Today", yesterday: "Yesterday", search: "Search title, project, Host or ID", noMatch: "No matching records", empty: "No records yet", records: "Records", macro: "Macro tasks", failed: "Read failed", helper: "Helper", calculation: "Routing calculation", command: "Command execution", input: "Input", recordDetail: "Record detail" },
] as const;

async function themeBeforeLoad(page: Page, theme: string) {
  await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
  await page.clock.setFixedTime(new Date("2026-09-27T10:00:00.000Z"));
}

async function listFits(page: Page, width: number) {
  const list = page.locator("[data-slot=records-list]");
  await expect(list).toBeVisible();
  await expectNoSidewaysScroll(page);
  const facts = await list.evaluate(node => {
    const frame = node.getBoundingClientRect();
    const viewport = node.querySelector<HTMLElement>("[data-slot=scroll-area-viewport]")!;
    const escaped = [...node.querySelectorAll<HTMLElement>("[data-slot=record-entry], [data-slot=records-search], h3")]
      .filter(item => { const box = item.getBoundingClientRect(); return box.left < frame.left - 1 || box.right > frame.right + 1; })
      .map(item => item.dataset.slot ?? item.tagName);
    const fonts = [...node.querySelectorAll<HTMLElement>("[data-slot=record-entry] *, h3, input")]
      .filter(item => item.getClientRects().length)
      .map(item => parseFloat(getComputedStyle(item).fontSize));
    return { width: frame.width, escaped, sideways: viewport.scrollWidth - viewport.clientWidth,
      fonts: fonts.filter(size => ![12, 14, 16, 18].includes(size)) };
  });
  expect(facts.width).toBeLessThanOrEqual(width);
  expect(facts.escaped).toEqual([]);
  expect(facts.sideways).toBeLessThanOrEqual(1);
  expect(facts.fonts).toEqual([]);
  expect(await list.locator("[data-slot=records-search]").evaluate(node => node.getBoundingClientRect().height)).toBe(32);
}

for (const w of words) for (const width of [1280, 390]) for (const theme of ["light", "dark"]) {
  test.describe(`records in ${w.locale}, ${width}px, ${theme}`, () => {
    test.use({ locale: w.locale, timezoneId: "UTC", viewport: { width, height: 800 }, reducedMotion: "reduce" });

    test("shows all kinds in day groups and opens each at the right address", async ({ page }) => {
      await themeBeforeLoad(page, theme);
      const commands: unknown[] = [];
      page.on("request", request => {
        if (request.method() === "POST" && new URL(request.url()).pathname === "/api/command") commands.push(request.postDataJSON());
      });
      await open(page, "#tasks/records");
      const list = page.locator("[data-slot=records-list]");
      await expect(list.locator("[data-slot=record-entry]")).toHaveCount(18);
      await expect(list.locator("h3")).toHaveCount(2);
      await expect(list.locator("h3").first()).toHaveText(w.today);
      await expect(list.locator("h3").last()).toHaveText(w.yesterday);
      await expect(list.locator("[data-run-id='preview-run-a1-router']")).toContainText("gpt-5-codex / high");
      await expect(list.locator(`[data-run-id='${ROUTING}']`)).toContainText(w.calculation);
      await expect(list.locator("[data-run-id='preview-command-c1']")).toBeVisible();
      await expect(list.locator(`[data-run-id='${COMMAND}']`)).toContainText(w.command);
      await expect(list.locator("[data-run-id='preview-run-b1-h1']")).toBeVisible();
      await expect(list.locator(`[data-run-id='${HELPER}']`)).toContainText(w.helper);
      await expect(list.getByRole("textbox", { name: w.search })).toBeVisible();
      await expect(list.getByRole("tab")).toHaveCount(0);
      await expect(list.getByRole("combobox")).toHaveCount(0);
      await expect(list.getByRole("checkbox")).toHaveCount(0);
      await listFits(page, width);

      await list.locator(`[data-run-id='${ROUTING}'] [data-slot=record-entry]`).click();
      await expect(page).toHaveURL(new RegExp(`#tasks/records/${ROUTING}$`));
      await expect(page.locator("[data-slot=task-detail]")).toBeVisible();
      await expect(page.locator("[data-slot=task-detail]")).toHaveAttribute("aria-label", w.recordDetail);
      await expect(page.locator("[data-slot=detail-band]")).toBeVisible();
      await expect(page.locator("[data-slot=task-detail-rail]")).toHaveCount(0);
      await expect(page.locator("[data-slot=task-detail]")).toContainText("Router");
      await expect(page.locator("[data-slot=detail-band]")).toContainText(w.input);
      await expectNoSidewaysScroll(page);
      await page.goBack();
      await expect(page).toHaveURL(/#tasks\/records$/);
      await expect(list).toBeVisible();

      await list.locator(`[data-run-id='${COMMAND}'] [data-slot=record-entry]`).click();
      await expect(page).toHaveURL(new RegExp(`#tasks/records/${COMMAND}$`));
      await expect(page.locator("[data-slot=task-detail]")).toBeVisible();
      await expect(page.locator("[data-slot=task-detail]")).toHaveAttribute("aria-label", w.recordDetail);
      await expect(page.locator("[data-slot=detail-band]")).toBeVisible();
      await expect(page.locator("[data-slot=detail-band]")).not.toContainText(w.input);
      await expect(page.locator("[data-slot=detail-band] > *")).toHaveCount(3);
      await expect(page.locator("[data-slot=task-detail-rail]")).toHaveCount(0);
      await page.goBack();
      await expect(page).toHaveURL(/#tasks\/records$/);

      await list.locator(`[data-run-id='${MICRO}'] [data-slot=record-entry]`).click();
      await expect(page).toHaveURL(new RegExp(`#tasks/macro-[^/]+/${MICRO}(?:/[1-9]\\d*)?$`));
      await expect(page.locator("[data-slot=detail-header]:visible")).toBeVisible();
      await expect(page.locator("[data-slot=detail-pane]:visible")).toBeVisible();
      if (width === 1280) await expect(page.locator("[data-slot=macro-list]")).toBeVisible();
      await page.goBack();
      await expect(page).toHaveURL(/#tasks\/records$/);
      await list.locator(`[data-run-id='${HELPER}'] [data-slot=record-entry]`).click();
      await expect(page).toHaveURL(new RegExp(`#tasks/macro-[^/]+/${HELPER}(?:/[1-9]\\d*)?$`));
      await expect(page.locator("[data-slot=detail-header]:visible")).toBeVisible();
      await expect(page.locator("[data-slot=detail-pane]:visible")).toBeVisible();
      expect(commands.every(item => ["workflow_get", "message_list", "selection_get"].includes((item as { operation: string }).operation))).toBe(true);
    });

    test("distinguishes no match, empty history and a failed read", async ({ page }) => {
      await themeBeforeLoad(page, theme);
      await open(page, "#tasks/records");
      const list = page.locator("[data-slot=records-list]");
      await expect(list.locator("[data-slot=record-entry]")).toHaveCount(18);
      await list.locator("[data-slot=records-search]").fill("no-such-record-ever");
      await expect(list.getByText(w.noMatch)).toBeVisible();
      await list.locator("[data-slot=records-search]").fill("");
      await expect(list.locator("[data-slot=record-entry]")).toHaveCount(18);
      await page.route("**/api/tasks?*", async route => route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ runs: null, total: 0, nextCursor: null }) }));
      await expect(list.getByRole("alert")).toBeVisible({ timeout: 7000 });
      await expect(list.locator("[data-slot=record-entry]")).toHaveCount(18);
      await expect(list.getByRole("alert")).toContainText(w.failed);
      await page.unroute("**/api/tasks?*");
      await page.route("**/api/tasks?*", async route => {
        const response = await route.fetch();
        const data = await response.json();
        await route.fulfill({ response, json: { ...data, runs: [], total: 0, nextCursor: null } });
      });
      await page.reload();
      await expect(list.getByText(w.empty)).toBeVisible();
      await expect(list.getByText(w.noMatch)).toHaveCount(0);
      await listFits(page, width);
    });
  });
}

test.describe("records pagination and polling", () => {
  test.use({ locale: "zh-CN", timezoneId: "UTC", viewport: { width: 1280, height: 800 } });

  test("loads an older page without losing the reading position", async ({ page }) => {
    await themeBeforeLoad(page, "light");
    let reads = 0;
    await page.route("**/api/tasks?*", async route => {
      reads++;
      const response = await route.fetch({ url: route.request().url().replace(/&before=[^&]+/, "") });
      const data = await response.json();
      const before = new URL(route.request().url()).searchParams.get("before");
      await route.fulfill({ response, json: { ...data, runs: before ? data.runs.slice(8) : data.runs.slice(0, 8),
        nextCursor: before ? null : "older-page" } });
    });
    await open(page, "#tasks/records");
    const list = page.locator("[data-slot=records-list]");
    await expect(list.locator("[data-slot=record-entry]")).toHaveCount(8);
    const viewport = list.locator("[data-slot=scroll-area-viewport]");
    await viewport.evaluate(node => { node.scrollTop = 80; });
    const before = await viewport.evaluate(node => node.scrollTop);
    await list.locator("[data-slot=records-more]").click();
    await expect(list.locator("[data-slot=record-entry]")).toHaveCount(18);
    expect(await viewport.evaluate(node => node.scrollTop)).toBeGreaterThanOrEqual(before);
    expect(reads).toBeGreaterThanOrEqual(2);
  });

  test("announces new first-page rows and stops polling while hidden", async ({ page }) => {
    await themeBeforeLoad(page, "light");
    let inject = false, reads = 0;
    await page.route("**/api/tasks?*", async route => {
      reads++;
      const response = await route.fetch();
      const data = await response.json();
      const fresh = { ...data.runs[0], runId: "preview-new-record", taskId: "preview-new-record",
        requestId: "preview-new-record", createdAt: "2026-09-27T10:01:00.000Z", updatedAt: "2026-09-27T10:01:00.000Z" };
      await route.fulfill({ response, json: inject ? { ...data, runs: [fresh, ...data.runs],
        total: data.total + 1 } : data });
    });
    await open(page, "#tasks/records");
    const list = page.locator("[data-slot=records-list]");
    await expect(list.locator("[data-slot=record-entry]")).toHaveCount(18);
    inject = true;
    await expect(list.locator("[data-slot=records-new]")).toBeVisible({ timeout: 7000 });
    await expect(list.locator("[data-slot=record-entry]")).toHaveCount(18);
    await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" }); document.dispatchEvent(new Event("visibilitychange")); });
    const hiddenReads = reads;
    await page.waitForTimeout(3300);
    expect(reads).toBe(hiddenReads);
    await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" }); document.dispatchEvent(new Event("visibilitychange")); });
    await expect.poll(() => reads).toBeGreaterThan(hiddenReads);
    await list.locator("[data-slot=records-new]").click();
    await expect(list.locator("[data-run-id=preview-new-record]")).toBeVisible();
    await expect(list.locator("[data-slot=records-new]")).toHaveCount(0);
    await settle(page);
  });
});

test.describe("task list geometry and records motion", () => {
  test.use({ locale: "zh-CN", timezoneId: "UTC", viewport: { width: 1280, height: 800 }, reducedMotion: "no-preference" });

  test("keeps four macro tabs evenly spaced and group controls at 32px", async ({ page }) => {
    await themeBeforeLoad(page, "light");
    await open(page, "#tasks");
    const list = page.locator("[data-slot=macro-list]");
    await expect(list).toBeVisible();
    const boxes = await list.locator("[data-slot=macro-filter-tabs] [role=tab]").evaluateAll(nodes =>
      nodes.map(node => { const box = node.getBoundingClientRect(); return { left: box.left, right: box.right, height: box.height }; }));
    expect(boxes).toHaveLength(4);
    const gaps = boxes.slice(1).map((box, index) => box.left - boxes[index].right);
    expect(Math.max(...gaps) - Math.min(...gaps)).toBeLessThanOrEqual(1);
    expect(boxes.every(box => box.height === 32)).toBe(true);
    await expect(list.locator("[data-slot=macro-group-heading]").first()).toBeVisible();
    const headings = await list.locator("[data-slot=macro-group-heading]").evaluateAll(nodes =>
      nodes.map(node => node.getBoundingClientRect().height));
    expect(headings.length).toBeGreaterThan(0);
    expect(headings.every(height => height === 32)).toBe(true);
  });

  test("animates records collapse and expansion through an intermediate width", async ({ page }) => {
    await themeBeforeLoad(page, "light");
    await open(page, "#tasks/records");
    const list = page.locator("[data-slot=records-list]");
    await expect(list.locator("[data-slot=record-entry]")).toHaveCount(18);
    const midway = async () => page.evaluate(async () => {
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const root = document.querySelector("[data-slot=records-list]")!.parentElement!.parentElement!;
      const animation = root.getAnimations().find(item => item.effect instanceof KeyframeEffect && item.effect.target === root);
      if (!animation) return null;
      const duration = Number(animation.effect!.getComputedTiming().endTime);
      animation.pause(); animation.currentTime = duration / 2;
      const width = parseFloat(getComputedStyle(root).gridTemplateColumns);
      animation.play();
      return { duration, width };
    });
    await list.getByRole("button", { name: "收起记录列表" }).click();
    const closing = await midway();
    expect(closing).not.toBeNull();
    expect(closing!.duration).toBeGreaterThanOrEqual(150);
    expect(closing!.width).toBeGreaterThan(0);
    expect(closing!.width).toBeLessThan(316);
    await settle(page);
    await page.getByRole("button", { name: "展开记录列表" }).click();
    const opening = await midway();
    expect(opening).not.toBeNull();
    expect(opening!.width).toBeGreaterThan(0);
    expect(opening!.width).toBeLessThan(316);
    await settle(page);
    await expect(list).toBeVisible();
  });
});

for (const locale of ["zh-CN", "en-US"]) test.describe(`record headers around 800px in ${locale}`, () => {
  test.use({ locale, timezoneId: "UTC", viewport: { width: 800, height: 800 }, reducedMotion: "reduce" });
  for (const id of [COMMAND, ROUTING]) test(`keeps exact dates and controls inside the header of ${id}`, async ({ page }) => {
    await themeBeforeLoad(page, "light");
    await open(page, `#tasks/records/${id}`);
    await expect(page.locator("[data-slot=detail-band]")).toBeVisible();
    const header = page.locator("[data-slot=detail-header]");
    const escaped = await header.evaluate(node => {
      const frame = node.getBoundingClientRect();
      return [...node.querySelectorAll<HTMLElement>("button, h2, dl > div, [data-slot=detail-status]")]
        .filter(item => { const box = item.getBoundingClientRect(); return box.left < frame.left - 1 || box.right > frame.right + 1; })
        .map(item => item.textContent);
    });
    expect(escaped).toEqual([]);
    await expectNoSidewaysScroll(page);
  });
});
