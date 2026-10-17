import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { expectNoSidewaysScroll, open, settle, test } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const shots = process.env.HMB_LIST_SHOTS;

const words = [
  { locale: "zh-CN", list: "宏任务列表", all: "全部", active: "进行中", host: "等待 Host", review: "等待验收", search: "搜索标题、项目、Host 或编号", noMatch: "没有匹配的宏任务", empty: "还没有宏任务", offline: "连接中断", retry: "重试连接", collapse: "收起宏任务列表", expand: "展开宏任务列表", more: "更多", stop: "停止宏任务", time: "时间统计", update: "有新的活动，按最近活动排序" },
  { locale: "en-US", list: "Macro task list", all: "All", active: "Running", host: "Waiting for Host", review: "Awaiting acceptance", search: "Search title, project, Host or ID", noMatch: "No matching macro tasks", empty: "No macro tasks yet", offline: "Disconnected", retry: "Reconnect", collapse: "Collapse macro task list", expand: "Expand macro task list", more: "More", stop: "Stop macro task", time: "Time statistics", update: "New activity, sort by latest activity" },
] as const;

async function photograph(page: Page, name: string, locale: string, width: number, theme: string) {
  if (!shots) return;
  await settle(page);
  await mkdir(shots, { recursive: true });
  await page.screenshot({ path: join(shots, `actual-${name}-${locale}-${width}-${theme}.png`), animations: "disabled" });
}

async function photographMidMotion(page: Page, name: string, scope = "[data-slot=macro-workspace]") {
  const motion = await page.evaluate(async selector => {
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const root = document.querySelector(selector)!;
    const running = root.getAnimations({ subtree: true }).filter(animation => {
      const end = animation.effect?.getComputedTiming().endTime;
      return end !== undefined && Number.isFinite(Number(end)) && Number(end) > 40 && animation.playState !== "finished";
    });
    for (const animation of running) {
      animation.pause();
      animation.currentTime = Number(animation.effect!.getComputedTiming().endTime) / 2;
    }
    return {
      count: running.length,
      samples: running.flatMap(animation => {
        const target = animation.effect instanceof KeyframeEffect ? animation.effect.target : null;
        if (!(target instanceof HTMLElement)) return [];
        const style = getComputedStyle(target);
        return [{ slot: target.dataset.slot ?? "", height: target.getBoundingClientRect().height,
          naturalHeight: parseFloat(style.getPropertyValue("--radix-collapsible-content-height")), opacity: Number(style.opacity),
          frames: animation.effect instanceof KeyframeEffect ? animation.effect.getKeyframes() : [] }];
      }),
    };
  }, scope);
  expect(motion.count, `${name} has visible motion`).toBeGreaterThan(0);
  const contentSlot = name.startsWith("mid-group-") ? "collapsible-content" : name.startsWith("mid-list-") ? "macro-sidebar" : null;
  if (contentSlot) {
    const sample = motion.samples.find(item => item.slot === contentSlot);
    if (shots) console.log(`${name} midpoint: ${JSON.stringify(sample)}`);
    expect(sample, `${name} animates ${contentSlot}`).toBeDefined();
    expect(sample!.height, `${name} has an intermediate height`).toBeGreaterThan(0);
    expect(Number.isFinite(sample!.naturalHeight), `${name} has a measured full height`).toBe(true);
    expect(sample!.height, `${name} has an intermediate height`).toBeLessThan(sample!.naturalHeight);
    expect(sample!.opacity, `${name} has intermediate opacity`).toBeGreaterThan(0);
    expect(sample!.opacity, `${name} has intermediate opacity`).toBeLessThan(1);
  }
  if (name.startsWith("mid-list-")) {
    const edges = await page.evaluate(() => {
      const sidebar = document.querySelector("[data-slot=macro-sidebar]")!.getBoundingClientRect();
      const detail = document.querySelector("[data-slot=macro-detail]")!.getBoundingClientRect();
      return { sidebarRight: sidebar.right, detailLeft: detail.left };
    });
    expect(edges.sidebarRight, `${name} sidebar stays within its grid column`).toBeLessThanOrEqual(edges.detailLeft + 1);
  }
  await expectNoSidewaysScroll(page);
  if (shots) {
    await mkdir(shots, { recursive: true });
    await page.screenshot({ path: join(shots, `actual-${name}-en-US-1280-light.png`), animations: "allow" });
  }
  await page.evaluate(() => document.getAnimations().filter(animation => animation.playState === "paused").forEach(animation => animation.play()));
  await settle(page);
}

async function themeBeforeLoad(page: Page, theme: string) {
  await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
}

async function assertListGeometry(page: Page, width: number) {
  const list = page.locator("[data-slot=macro-list]");
  await expect(list).toBeVisible();
  await expectNoSidewaysScroll(page);
  const errors = await list.evaluate(node => {
    const frame = node.getBoundingClientRect();
    const offenders: string[] = [];
    for (const element of node.querySelectorAll<HTMLElement>("[data-slot=macro-list-heading], [data-slot=macro-search], [data-slot=macro-filter-tabs], [data-slot=macro-group-heading], [data-slot=macro-entry]")) {
      if (!element.getClientRects().length) continue;
      const box = element.getBoundingClientRect();
      if (box.left < frame.left - 1 || box.right > frame.right + 1) offenders.push(`${element.className}: outside list`);
      if (element.scrollWidth > element.clientWidth + 1 && element.dataset.slot !== "macro-entry") offenders.push(`${element.dataset.slot}: clipped ${element.scrollWidth}/${element.clientWidth}`);
    }
    for (const element of node.querySelectorAll<HTMLElement>("[data-slot=macro-entry] time, [data-slot=macro-entry-counts], [data-slot=macro-group-heading] span, [data-slot=macro-filter-tabs] [data-slot=tabs-trigger]")) {
      if (element.scrollWidth > element.clientWidth + 1) offenders.push(`${element.className}: text clipped`);
    }
    return offenders;
  });
  expect(errors).toEqual([]);
  const listBox = (await list.boundingBox())!;
  expect(listBox.width).toBeLessThanOrEqual(width);
  expect(listBox.width).toBeGreaterThan(width === 390 ? 350 : 290);
  const viewport = list.locator("[data-slot=scroll-area-viewport]");
  const scrollWidth = await viewport.evaluate(node => ({ content: node.scrollWidth, frame: node.clientWidth }));
  expect(scrollWidth.content, "macro list rows fit the scroll viewport").toBeLessThanOrEqual(scrollWidth.frame + 1);
  const search = list.locator("[data-slot=macro-search]");
  expect(await search.evaluate(node => getComputedStyle(node).fontSize)).toBe("14px");
  expect((await search.boundingBox())!.height).toBeGreaterThanOrEqual(32);
  const controls = await list.locator("[data-slot=macro-list-heading], [data-slot=macro-search], [data-slot=macro-filter-tabs]").evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect()));
  expect(controls[0]!.bottom).toBeLessThanOrEqual(controls[1]!.top + 1);
  expect(controls[1]!.bottom).toBeLessThanOrEqual(controls[2]!.top + 1);
  const metrics = await list.evaluate(node => {
    const measure = (selector: string) => {
      const item = node.querySelector<HTMLElement>(selector)!;
      return { font: parseFloat(getComputedStyle(item).fontSize), height: item.getBoundingClientRect().height };
    };
    return { navigation: measure("[data-slot=macro-list-heading]"), search: measure("[data-slot=macro-search]"),
      tab: measure("[data-slot=tabs-trigger]"), group: measure("[data-slot=macro-group-heading]"), entry: measure("[data-slot=macro-entry]") };
  });
  expect(metrics.navigation.font).toBeGreaterThanOrEqual(14);
  expect(metrics.search.height).toBeGreaterThanOrEqual(32);
  expect(metrics.tab.height).toBeGreaterThanOrEqual(32);
  expect(metrics.group.font).toBeGreaterThanOrEqual(12);
  expect(metrics.entry.font).toBeGreaterThanOrEqual(14);
}

async function assertSelectedTabUnderline(page: Page) {
  const tab = page.locator("[data-slot=macro-filter-tabs] [role=tab][aria-selected=true]");
  const after = await tab.evaluate(node => {
    const style = getComputedStyle(node, "::after");
    return { content: style.content, opacity: Number(style.opacity), height: parseFloat(style.height), background: style.backgroundColor };
  });
  expect(after.content).not.toBe("none");
  expect(after.opacity).toBe(1);
  expect(after.height).toBeGreaterThanOrEqual(2);
  expect(after.background).not.toBe("rgba(0, 0, 0, 0)");
}

async function assertHeaderGeometry(page: Page) {
  const header = page.locator("[data-slot=macro-overview-header]");
  await expect(header).toBeVisible();
  await expectNoSidewaysScroll(page);
  const clipped = await header.evaluate(node => [...node.querySelectorAll<HTMLElement>("dt, dd, button:not([data-slot=macro-menu-button])")]
    .filter(element => element.getClientRects().length && (element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1))
    .map(element => element.textContent?.trim().slice(0, 40)));
  expect(clipped, "macro header text is clipped").toEqual([]);
  const boxes = await header.locator(":scope > div").evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().toJSON()));
  expect(boxes).toHaveLength(2);
  expect(boxes[0]!.right <= boxes[1]!.left + 1 || boxes[0]!.bottom <= boxes[1]!.top + 1,
    "header identity and controls do not overlap").toBe(true);
  const escaped = await header.evaluate(node => {
    const frame = node.getBoundingClientRect();
    return [...node.querySelectorAll<HTMLElement>("[data-slot=macro-menu-button], dl dd")]
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
      await expect(page).toHaveURL(new RegExp(`#tasks/${A}$`));
      const list = page.locator("[data-slot=macro-list]");
      await expect(list).toBeVisible();
      await expect(list.locator("[data-slot=macro-entry]")).toHaveCount(2);
      await expect(list.locator("[data-slot=macro-group-heading]")).toHaveCount(3);
      await expect(list.locator("[data-slot=macro-group-heading]").last()).toHaveAttribute("aria-expanded", "false");
      await assertListGeometry(page, width);
      await assertSelectedTabUnderline(page);
      await list.locator("[data-slot=macro-group-heading]").nth(1).click();
      await photograph(page, "list", w.locale, width, theme);
      await list.locator("[data-slot=macro-group-heading]").nth(1).click();

      for (const [key, label] of [["active", w.active], ["host", w.host], ["review", w.review], ["all", w.all]] as const) {
        const read = page.waitForResponse(response => {
          const url = new URL(response.url());
          return url.pathname === "/api/macro-tasks" && url.searchParams.get("filter") === key;
        });
        await list.getByRole("tab", { name: new RegExp(`^${label}`) }).click();
        await read;
        await expect(list.getByRole("tab", { name: new RegExp(`^${label} [1-9]`) })).toHaveAttribute("aria-selected", "true");
        await expect(list.getByRole("status")).toHaveCount(0);
        await expect(list.locator("[data-slot=macro-filter-tabs]")).toBeVisible();
        await assertSelectedTabUnderline(page);
        await photograph(page, `filter-${key}`, w.locale, width, theme);
      }
      await list.getByRole("textbox", { name: w.search }).fill("no-such-macro-task");
      await expect(list.getByText(w.noMatch)).toBeVisible();
      await photograph(page, "unmatched", w.locale, width, theme);
      await list.getByRole("textbox", { name: w.search }).fill("");
      await expect(list.locator("[data-slot=macro-entry]")).toHaveCount(2);

      await list.locator("[data-slot=macro-group-heading]").last().click();
      await expect(list.locator("[data-slot=macro-entry]")).toHaveCount(3);
      await list.locator("[data-slot=macro-group-heading]").last().click();
      await list.locator("[data-macro-task-id]").first().locator("[data-slot=macro-entry]").click();
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
      await expect(list).toBeHidden();
      await expect(page.getByRole("button", { name: w.expand })).toBeVisible();
      await photograph(page, "list-collapsed", w.locale, width, theme);
      await page.getByRole("button", { name: w.expand }).click();
      await expect(list).toBeVisible();
    });

    test("shows an empty board and preserves existing rows after an interrupted read", async ({ page }) => {
      await themeBeforeLoad(page, theme);
      await page.route(/\/api\/macro-tasks(?:\?|$)/, route => route.fulfill({ json: {
        macroTasks: [], totals: { all: 0, active: 0, host: 0, review: 0 }, nextCursor: null, cursor: 21, changed: false,
      } }));
      await open(page, "#tasks");
      await expect(page.getByRole("heading", { name: w.empty })).toBeVisible();
      await expect(page.locator("[data-slot=macro-first-use]")).toBeVisible();
      await expect(page.locator("[data-slot=macro-search]")).toHaveCount(0);
      await photograph(page, "empty", w.locale, width, theme);
      await page.unrouteAll();
      await page.reload();
      await expect(page.locator("[data-slot=macro-entry]")).toHaveCount(2);
      await page.route(/\/api\/macro-tasks(?:\?|$)/, route => route.fulfill({ json: { malformed: true } }));
      await expect(page.getByRole("alert").filter({ hasText: w.offline })).toBeVisible({ timeout: 7000 });
      await expect(page.locator("[data-slot=macro-offline]")).toBeVisible();
      await expect(page.locator("[data-slot=macro-entry]")).toHaveCount(2);
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
    const list = page.locator("[data-slot=macro-list]");
    const first = list.locator("[data-macro-task-id]").first();
    await expect(first).toHaveAttribute("data-macro-task-id", id(0));
    await page.mouse.move(1200, 10);
    updated = 1;
    await expect(first).toHaveAttribute("data-macro-task-id", id(1), { timeout: 7000 });
    await list.locator(`[data-macro-task-id="${A}"] button`).click();
    await expect(page.locator("[data-slot=macro-overview-header]")).toBeVisible();
    await list.hover({ position: { x: 12, y: 12 } });
    updated = 2;
    const marker = list.getByRole("button", { name: w.update });
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

test.describe("macro list motion", () => {
  test.use({ locale: "en-US", viewport: { width: 1280, height: 800 }, reducedMotion: "no-preference" });
  test("captures intermediate group, whole-list, row and tab states", async ({ page }) => {
    await themeBeforeLoad(page, "light");
    await open(page, "#tasks");
    const list = page.locator("[data-slot=macro-list]");
    const group = list.locator("[data-slot=macro-group]").last();
    const heading = group.locator("[data-slot=macro-group-heading]");
    await expect(heading).toHaveAttribute("aria-expanded", "false");
    await heading.click();
    await photographMidMotion(page, "mid-group-expand");
    await expect(heading).toHaveAttribute("aria-expanded", "true");
    await heading.click();
    await photographMidMotion(page, "mid-group-collapse");
    await expect(heading).toHaveAttribute("aria-expanded", "false");

    await list.locator("[data-macro-task-id]").nth(1).getByRole("button").click();
    await photographMidMotion(page, "mid-row-select");
    await expect(list.locator("[data-macro-task-id]").nth(1).getByRole("button")).toHaveAttribute("aria-pressed", "true");
    await list.getByRole("tab", { name: /^Running/ }).click();
    await photographMidMotion(page, "mid-tab-select");
    await expect(list.getByRole("tab", { name: /^Running/ })).toHaveAttribute("aria-selected", "true");

    await list.getByRole("button", { name: "Collapse macro task list" }).click();
    await photographMidMotion(page, "mid-list-collapse");
    await expect(list).toBeHidden();
    await page.getByRole("button", { name: "Expand macro task list" }).click();
    await photographMidMotion(page, "mid-list-expand");
    await expect(list).toBeVisible();
  });
});

test.describe("macro list reduced motion", () => {
  test.use({ locale: "en-US", viewport: { width: 1280, height: 800 }, reducedMotion: "reduce" });
  test("settles group and list changes without long-running motion", async ({ page }) => {
    await open(page, "#tasks");
    const list = page.locator("[data-slot=macro-list]");
    const group = list.locator("[data-slot=macro-group]").last();
    await group.locator("[data-slot=macro-group-heading]").click();
    await settle(page);
    await expect(group.locator("[data-slot=macro-group-heading]")).toHaveAttribute("aria-expanded", "true");
    await list.getByRole("button", { name: "Collapse macro task list" }).click();
    await settle(page);
    await expect(list).toBeHidden();
    const motions = await page.evaluate(() => document.getAnimations().filter(animation => {
      const end = animation.effect?.getComputedTiming().endTime;
      return end !== undefined && Number.isFinite(Number(end)) && Number(end) > 40 && animation.playState === "running";
    }).length);
    expect(motions).toBe(0);
    await expectNoSidewaysScroll(page);

    const header = page.locator("[data-slot=macro-overview-header]");
    await header.getByRole("button", { name: "Time statistics" }).click();
    const stats = page.getByRole("dialog", { name: "Time statistics" });
    await expect(stats).toBeVisible();
    expect(await stats.evaluate(node => getComputedStyle(node).animationName)).toBe("none");
    await page.keyboard.press("Escape");

    await header.getByRole("button", { name: "More" }).click();
    const stopAction = page.getByRole("button", { name: "Stop macro task" });
    await expect(stopAction).toBeVisible();
    expect(await page.getByRole("dialog").evaluate(node => getComputedStyle(node).animationName)).toBe("none");
    await stopAction.click();
    const confirmation = page.getByRole("dialog");
    await expect(confirmation).toBeVisible();
    expect(await confirmation.evaluate(node => getComputedStyle(node).animationName)).toBe("none");
  });
});

// State colour belongs to actionable work; selection contributes only its wash.
for (const width of [1280, 390]) for (const theme of ["light", "dark"]) {
  test.describe(`macro state edges, ${width}px, ${theme}`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: width === 390 ? 844 : 800 }, reducedMotion: "reduce" });
    test("keeps waiting and live edges, count shapes and accessible state names across selection", async ({ page }) => {
      await themeBeforeLoad(page, theme);
      await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
        const response = await route.fetch();
        const data = await response.json();
        const original = data.macroTasks[0];
        const variants = [
          { title: "等待接手", host: 1, review: 1, active: 1, failed: 0, accepted: 0, ended: 0 },
          { title: "进行中的任务", host: 0, review: 0, active: 1, failed: 0, accepted: 0, ended: 0 },
          { title: "Host 已给结论的失败", host: 0, review: 0, active: 0, failed: 1, accepted: 0, ended: 1 },
          { title: "全部验收", host: 0, review: 0, active: 0, failed: 0, accepted: 1, ended: 1 },
        ];
        data.macroTasks = variants.map(({ title, ...counts }, index) => ({ ...original,
          macroTaskId: index === 1 ? A : `macro-edge-${index}`, title, titleSource: "macro-task",
          state: index === 0 ? "host" : index === 1 ? "active" : "ended",
          counts: { ...original.counts, roots: counts.host + counts.review + counts.active + counts.ended, ...counts },
          lastActivitySeq: 100 - index,
        }));
        data.totals = { all: 4, host: 1, active: 2, review: 1 };
        data.nextCursor = null; data.changed = false;
        await route.fulfill({ response, json: data });
      });
      await page.route(/\/api\/macro-tasks\/macro-edge-3\/timeline(?:\?|$)/, async route => {
        const response = await route.fetch({ url: route.request().url().replace("macro-edge-3", A) });
        const data = await response.json();
        data.macroTask = { ...data.macroTask, macroTaskId: "macro-edge-3", title: "全部验收", state: "ended",
          counts: { ...data.macroTask.counts, roots: 1, host: 0, review: 0, active: 0, failed: 0, accepted: 1, ended: 1 } };
        await route.fulfill({ response, json: data });
      });
      // Start with a finished row selected, so its selection cannot create an edge.
      await open(page, "#tasks/macro-edge-3");
      const list = page.locator("[data-slot=macro-list]");
      if (width === 390) await page.locator("[data-slot=macro-overview-header]").getByRole("button", { name: /宏任务/ }).click();
      await expect(list).toBeVisible();
      const rows = list.locator("[data-slot=macro-entry]");
      await expect(rows).toHaveCount(4);
      for (const [index, state] of ["等待接手", "进行中", "失败", "已验收"].entries()) {
        await expect(rows.nth(index)).toHaveAccessibleName(new RegExp(state));
      }
      expect(await rows.locator(":scope > [data-slot=state-mark]").count()).toBe(0);
      await expect(rows.nth(2).locator("[data-slot=macro-entry-counts] [data-kind=fail] path")).toHaveCount(1);
      const styles = () => rows.evaluateAll(nodes => nodes.map(node => {
        const edge = getComputedStyle(node, "::before");
        const style = getComputedStyle(node);
        const token = (name: string) => {
          const probe = document.createElement("span"); probe.style.color = `var(${name})`; node.append(probe);
          const colour = getComputedStyle(probe).color; probe.remove(); return colour;
        };
        return { content: edge.content, width: edge.width, colour: edge.backgroundColor,
          background: style.backgroundColor, border: style.borderLeftWidth,
          hand: token("--hand-mark"), live: token("--live"), wash: token("--live-wash"), transition: style.transitionProperty };
      }));
      let measured = await styles();
      expect(measured[0]).toMatchObject({ width: "3px", colour: measured[0].hand });
      expect(measured[1]).toMatchObject({ width: "3px", colour: measured[1].live });
      for (const index of [2, 3]) expect(measured[index].content).toBe("none");
      expect(measured[3].background).toBe(measured[3].wash);
      expect(measured.every(row => row.border === "0px" && row.transition === "none")).toBe(true);
      await assertListGeometry(page, width);
      await photograph(page, "edges-list", "zh-CN", width, theme);
      await rows.nth(1).click();
      if (width === 390) await page.locator("[data-slot=macro-overview-header]").getByRole("button", { name: /宏任务/ }).click();
      await settle(page);
      await expect(rows.nth(1)).toHaveAttribute("aria-pressed", "true");
      await rows.nth(1).hover(); await settle(page);
      measured = await styles();
      expect(measured[1]).toMatchObject({ width: "3px", colour: measured[1].live, background: measured[1].wash, border: "0px" });
      await photograph(page, "edges-selected-live", "zh-CN", width, theme);
      await expectNoSidewaysScroll(page);
    });
  });
}
