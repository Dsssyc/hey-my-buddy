import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { expectNoSidewaysScroll, open, settle, test } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const shots = process.env.HMB_LIST_SHOTS;

const words = [
  { locale: "zh-CN", list: "宏任务列表", all: "全部", active: "进行中", host: "等待 Host", review: "等待验收", search: "搜索标题、项目、Host 或编号", noMatch: "没有匹配的宏任务", empty: "还没有宏任务", offline: "连接中断", retry: "重试连接", collapse: "收起宏任务列表", expand: "展开宏任务列表", more: "更多", stop: "停止宏任务", time: "时间统计" },
  { locale: "en-US", list: "Macro task list", all: "All", active: "Running", host: "Waiting for Host", review: "Awaiting acceptance", search: "Search title, project, Host or ID", noMatch: "No matching macro tasks", empty: "No macro tasks yet", offline: "Disconnected", retry: "Reconnect", collapse: "Collapse macro task list", expand: "Expand macro task list", more: "More", stop: "Stop macro task", time: "Time statistics" },
] as const;

async function photograph(page: Page, name: string, locale: string, width: number, theme: string) {
  if (!shots) return;
  await settle(page);
  await mkdir(shots, { recursive: true });
  await page.screenshot({ path: join(shots, `actual-${name}-${locale}-${width}-${theme}.png`), animations: "disabled" });
}

async function themeBeforeLoad(page: Page, theme: string) {
  await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
}

async function assertListGeometry(page: Page, width: number) {
  const list = page.locator(".macro-list");
  await expect(list).toBeVisible();
  await expectNoSidewaysScroll(page);
  const errors = await list.evaluate(node => {
    const frame = node.getBoundingClientRect();
    const offenders: string[] = [];
    for (const element of node.querySelectorAll<HTMLElement>(".macro-list-heading, .macro-search, .macro-filter-tabs, .macro-group-heading, .macro-entry")) {
      if (!element.getClientRects().length) continue;
      const box = element.getBoundingClientRect();
      if (box.left < frame.left - 1 || box.right > frame.right + 1) offenders.push(`${element.className}: outside list`);
      if (element.scrollWidth > element.clientWidth + 1 && !element.classList.contains("macro-entry")) offenders.push(`${element.className}: clipped ${element.scrollWidth}/${element.clientWidth}`);
    }
    for (const element of node.querySelectorAll<HTMLElement>(".macro-entry time, .macro-entry-counts, .macro-group-heading span, .macro-filter-tabs [data-slot=tabs-trigger]")) {
      if (element.scrollWidth > element.clientWidth + 1) offenders.push(`${element.className}: text clipped`);
    }
    return offenders;
  });
  expect(errors).toEqual([]);
  const listBox = (await list.boundingBox())!;
  expect(listBox.width).toBe(width === 390 ? 390 : 316);
  const search = list.locator(".macro-search");
  expect(await search.evaluate(node => getComputedStyle(node).fontSize)).toBe("14px");
  expect((await search.boundingBox())!.height).toBe(32);
  const controls = await list.locator(".macro-list-heading, .macro-search, .macro-filter-tabs").evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect()));
  expect(controls[0]!.bottom).toBeLessThanOrEqual(controls[1]!.top + 1);
  expect(controls[1]!.bottom).toBeLessThanOrEqual(controls[2]!.top + 1);
  const metrics = await list.evaluate(node => {
    const measure = (selector: string) => {
      const item = node.querySelector<HTMLElement>(selector)!;
      return { font: parseFloat(getComputedStyle(item).fontSize), height: item.getBoundingClientRect().height };
    };
    return { navigation: measure(".macro-list-navigation button"), search: measure(".macro-search"),
      tab: measure("[data-slot=tabs-trigger]"), group: measure(".macro-group-heading"), entry: measure(".macro-entry") };
  });
  expect(metrics.navigation.font).toBeGreaterThanOrEqual(14);
  expect(metrics.search.height).toBeGreaterThanOrEqual(32);
  expect(metrics.tab.height).toBeGreaterThanOrEqual(32);
  expect(metrics.group.font).toBeGreaterThanOrEqual(12);
  expect(metrics.entry.font).toBeGreaterThanOrEqual(14);
}

async function assertHeaderGeometry(page: Page) {
  const header = page.locator("[data-slot=macro-overview-header]");
  await expect(header).toBeVisible();
  await expectNoSidewaysScroll(page);
  const clipped = await header.evaluate(node => [...node.querySelectorAll<HTMLElement>("h2, dt, dd, button:not(.macro-menu-button)")]
    .filter(element => element.getClientRects().length && (element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1))
    .map(element => element.textContent?.trim().slice(0, 40)));
  expect(clipped, "macro header text is clipped").toEqual([]);
  const boxes = await header.locator(".macro-overview-leading, .macro-overview-trailing").evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().toJSON()));
  expect(boxes).toHaveLength(2);
  expect(boxes[0]!.right <= boxes[1]!.left + 1 || boxes[0]!.bottom <= boxes[1]!.top + 1,
    "header identity and controls do not overlap").toBe(true);
  const escaped = await header.evaluate(node => {
    const frame = node.getBoundingClientRect();
    return [...node.querySelectorAll<HTMLElement>(".macro-overview-count, .macro-menu-button")]
      .filter(item => { const box = item.getBoundingClientRect(); return box.left < frame.left - 1 || box.right > frame.right + 1; })
      .map(item => item.textContent?.trim());
  });
  expect(escaped, "header counts and controls stay in the header").toEqual([]);
  const titleSize = await header.locator("h2").evaluate(node => parseFloat(getComputedStyle(node).fontSize));
  expect(titleSize).toBeGreaterThanOrEqual(18);
}

for (const w of words) for (const width of [1280, 390]) for (const theme of ["light", "dark"]) {
  test.describe(`macro list and header in ${w.locale}, ${width}px, ${theme}`, () => {
    test.use({ locale: w.locale, viewport: { width, height: width === 390 ? 844 : 800 }, reducedMotion: "reduce" });

    test("groups, filters, search, fold, menu and time statistics stay usable", async ({ page }) => {
      await themeBeforeLoad(page, theme);
      await open(page, "#tasks");
      const list = page.getByRole("region", { name: w.list });
      await expect(list.locator(".macro-entry")).toHaveCount(2);
      await expect(list.locator(".macro-group-heading")).toHaveCount(3);
      await expect(list.locator(".macro-group-heading").last()).toHaveAttribute("aria-expanded", "false");
      await assertListGeometry(page, width);
      await list.locator(".macro-group-heading").nth(1).click();
      await photograph(page, "list", w.locale, width, theme);
      await list.locator(".macro-group-heading").nth(1).click();

      for (const [key, label] of [["active", w.active], ["host", w.host], ["review", w.review], ["all", w.all]] as const) {
        const read = page.waitForResponse(response => {
          const url = new URL(response.url());
          return url.pathname === "/api/macro-tasks" && url.searchParams.get("filter") === key;
        });
        await list.getByRole("tab", { name: new RegExp(`^${label}`) }).click();
        await read;
        await expect(list.getByRole("tab", { name: new RegExp(`^${label} [1-9]`) })).toHaveAttribute("aria-selected", "true");
        await expect(list.getByRole("status")).toHaveCount(0);
        await expect(list.locator(".macro-filter-tabs")).toBeVisible();
        await photograph(page, `filter-${key}`, w.locale, width, theme);
      }
      await list.getByRole("textbox", { name: w.search }).fill("no-such-macro-task");
      await expect(list.getByText(w.noMatch)).toBeVisible();
      await photograph(page, "unmatched", w.locale, width, theme);
      await list.getByRole("textbox", { name: w.search }).fill("");
      await expect(list.locator(".macro-entry")).toHaveCount(2);

      await list.locator(".macro-group-heading").last().click();
      await expect(list.locator(".macro-entry")).toHaveCount(3);
      await list.locator(".macro-group-heading").last().click();
      await list.locator("[data-macro-task-id]").first().locator(".macro-entry").click();
      await expect(page).toHaveURL(new RegExp(`#tasks/${A}$`));
      await assertHeaderGeometry(page);
      await photograph(page, "detail", w.locale, width, theme);

      const header = page.locator("[data-slot=macro-overview-header]");
      await header.getByRole("button", { name: w.time }).click();
      await expect(page.getByRole("dialog", { name: w.time })).toBeVisible();
      await photograph(page, "time", w.locale, width, theme);
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog", { name: w.time })).toBeHidden();

      await header.getByRole("button", { name: w.more }).click();
      await expect(page.getByRole("button", { name: w.stop })).toBeVisible();
      await photograph(page, "menu", w.locale, width, theme);
      await page.getByRole("button", { name: w.stop }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await photograph(page, "stop-confirm", w.locale, width, theme);
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog")).toBeHidden();

      if (width === 1280) {
        await list.getByRole("button", { name: w.collapse }).click();
      } else {
        await header.getByRole("button", { name: /宏任务|Macro tasks/ }).click();
        await list.getByRole("button", { name: w.collapse }).click();
      }
      await expect(page.locator(".macro-workspace")).toHaveClass(/list-collapsed/);
      await photograph(page, "list-collapsed", w.locale, width, theme);
      await page.getByRole("button", { name: w.expand }).click();
      await expect(page.locator(".macro-workspace")).not.toHaveClass(/list-collapsed/);
    });

    test("shows an empty board and preserves existing rows after an interrupted read", async ({ page }) => {
      await themeBeforeLoad(page, theme);
      await page.route(/\/api\/macro-tasks(?:\?|$)/, route => route.fulfill({ json: {
        macroTasks: [], totals: { all: 0, active: 0, host: 0, review: 0 }, nextCursor: null, cursor: 21, changed: false,
      } }));
      await open(page, "#tasks");
      await expect(page.getByRole("heading", { name: w.empty })).toBeVisible();
      await expect(page.locator(".macro-search")).toHaveCount(0);
      await photograph(page, "empty", w.locale, width, theme);
      await page.unrouteAll();
      await page.reload();
      await expect(page.locator(".macro-entry")).toHaveCount(2);
      await page.route(/\/api\/macro-tasks(?:\?|$)/, route => route.fulfill({ json: { malformed: true } }));
      await expect(page.getByRole("alert").filter({ hasText: w.offline })).toBeVisible({ timeout: 7000 });
      await expect(page.locator(".macro-entry")).toHaveCount(2);
      await expect(page.getByRole("button", { name: w.retry })).toBeVisible();
      await photograph(page, "offline", w.locale, width, theme);
    });
  });
}

// The former interaction probe: a poll refreshes facts in place, and the reader
// decides when a new activity order can move under the pointer or a scroll.
for (const w of words) test.describe(`stable macro task order in ${w.locale}`, () => {
  test.use({ locale: w.locale, viewport: { width: 1280, height: 800 } });
  test("defers activity under a pointer and below the top, with the selection retained", async ({ page }) => {
    let updated = 0;
    const id = (index: number) => index === 0 ? A : `macro-90000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
    await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
      const response = await route.fetch();
      const data = await response.json();
      const original = data.macroTasks.find((row: { kind: string }) => row.kind === "macro-task");
      data.macroTasks = Array.from({ length: 24 }, (_, index) => ({ ...original, macroTaskId: id(index),
        title: `Synthetic ${index}`, lastActivitySeq: updated && index === updated ? 1000 + index : 100 - index,
      })).sort((a, b) => b.lastActivitySeq - a.lastActivitySeq);
      data.totals = { all: 24, active: 24, host: 24, review: 24 };
      data.nextCursor = null; data.changed = false;
      await route.fulfill({ response, json: data });
    });
    await open(page, "#tasks");
    const list = page.locator(".macro-list");
    const first = list.locator("[data-macro-task-id]").first();
    await expect(first).toHaveAttribute("data-macro-task-id", id(0));
    await page.mouse.move(1200, 10);
    updated = 1;
    await expect(first).toHaveAttribute("data-macro-task-id", id(1), { timeout: 7000 });
    await list.locator(`[data-macro-task-id="${A}"] button`).click();
    await expect(page.locator("[data-slot=macro-overview-header]")).toBeVisible();
    await list.hover({ position: { x: 12, y: 12 } });
    updated = 2;
    const marker = list.locator(".macro-update");
    await expect(marker).toBeVisible({ timeout: 7000 });
    await expect(first).toHaveAttribute("data-macro-task-id", id(1));
    await marker.click();
    await expect(first).toHaveAttribute("data-macro-task-id", id(2));
    await expect(list.locator(`[data-macro-task-id="${A}"] button`)).toHaveAttribute("aria-pressed", "true");
    const viewport = list.locator("[data-slot=scroll-area-viewport]");
    await viewport.hover(); await page.mouse.wheel(0, 400);
    await expect.poll(() => viewport.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
    updated = 3;
    await expect(marker).toBeVisible({ timeout: 7000 });
    await expect(first).toHaveAttribute("data-macro-task-id", id(2));
    await page.mouse.move(1200, 10);
    await viewport.focus(); await viewport.press("Home");
    await expect(first).toHaveAttribute("data-macro-task-id", id(3), { timeout: 7000 });
    await expect(list.locator(`[data-macro-task-id="${A}"] button`)).toHaveAttribute("aria-pressed", "true");
  });
});
