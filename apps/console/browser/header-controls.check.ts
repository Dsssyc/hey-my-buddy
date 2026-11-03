import { expect, type Locator, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, settle, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const captures = fileURLToPath(new URL("../../../tmp/console-header", import.meta.url));
const longTitle = "存储批：仓库身份、目标说明、空壳的撤销记录与升级到 schema 33 的执行计划。".repeat(3);
const goals = [3, 2, 1].map(revision => ({ revision, createdAt: `2026-11-0${revision}T04:55:00Z`,
  body: `第 ${revision} 版的正文。\n\n下一个存储批：有 Git 时仓库靠 Git 里的标记引用认、路径只用来找到它；宏任务有一份可修改、留历史的目标说明；没有任务的空壳在删除失败或中断后仍能证明归属。先盘点，写执行计划，再分微任务实现，一次升级。` }));

async function setup(page: Page, language: string, long = false, single = false) {
  await page.setViewportSize({ width: 1280, height: 850 });
  await page.addInitScript(language => {
    localStorage.setItem("hey-my-buddy.console.theme", "dark");
    localStorage.setItem("hey-my-buddy.language", language);
  }, language);
  await page.route(`**/api/macro-tasks/${macro}/timeline?*`, async route => {
    const response = await route.fetch(), data = await response.json();
    data.macroTask.goal = single ? goals[2] : goals[0];
    data.macroTask.title = long ? longTitle : "控制台侧栏与信息行的版面";
    data.macroTask.titleSource = "macro-task";
    if (long) { data.rows[0].title = longTitle; data.rows[0].titleSource = "title"; }
    await route.fulfill({ response, json: data });
  });
  await page.route(`**/api/macro-tasks/${macro}/goals`, route => route.fulfill({ json: { macroTaskId: macro, goals: single ? goals.slice(2) : goals } }));
  if (long) await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
    const response = await route.fetch(), data = await response.json();
    for (const group of data.groups) {
      if (group.project) group.project.label = longTitle;
      for (const task of group.macroTasks) { task.title = longTitle; if (task.project) task.project.label = longTitle; }
    }
    await route.fulfill({ response, json: data });
  });
  await open(page, `#tasks/${macro}`);
  await expect(page.locator('[data-slot="macro-goal-trigger"]')).toBeVisible();
  await expect(page.locator('[data-slot="macro-entry"]').first()).toBeVisible();
}

async function shot(page: Page, name: string) {
  await mkdir(captures, { recursive: true });
  await page.screenshot({ path: `${captures}/${name}.png` });
}

async function noHint(page: Page, target: Locator) {
  await target.hover();
  await page.waitForTimeout(700);
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await expect(target).not.toHaveAttribute("title");
  await page.mouse.move(0, 0);
}

for (const language of ["zh", "en"]) test(`header controls and goal geometry ${language}`, async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await setup(page, language);
  const header = page.locator('[data-slot="macro-overview-header"]');
  const title = header.locator("h2");
  const trigger = header.locator('[data-slot="macro-goal-trigger"]');
  const before = (await title.boundingBox())!, button = (await trigger.boundingBox())!;
  expect(button.x - before.x - before.width, "goal button immediately follows the title").toBeCloseTo(6, 0);
  expect(Math.abs(button.y + button.height / 2 - before.y - before.height / 2)).toBeLessThan(1);
  await expect(trigger).toHaveAttribute("data-size", "icon-sm");
  await expect(header.locator('[data-slot="macro-overview-facts"] dt')).toHaveText(language === "zh" ? ["项目", "Host", "历时", "ID"] : ["Project", "Host", "Elapsed", "ID"]);
  await expect(title).toHaveAttribute("data-overflow", "false");
  await noHint(page, title);
  await trigger.hover();
  await expect(page.getByRole("tooltip")).toHaveText(language === "zh" ? "目标说明" : "Goal");
  await page.mouse.move(0, 0);
  if (language === "zh") await shot(page, "zh-dark-1280-header");
  await trigger.click();
  const card = page.locator('[data-slot="macro-goal-card"]');
  await expect(card.locator(".goal-version")).toHaveCount(3);
  const bounds = (await card.boundingBox())!;
  expect(bounds.y - button.y - button.height, "card opens 4px below the button").toBeCloseTo(4, 1);
  expect(await card.evaluate(element => element.getAnimations().length)).toBe(0);
  const geometry = await card.evaluate(element => {
    const history = element.querySelector(".goal-history")!, body = element.querySelector(".goal-body")!;
    const head = element.querySelector(".goal-heading")!.getBoundingClientRect();
    const heading = history.querySelector("h3")!.getBoundingClientRect();
    const version = history.querySelector(".goal-version-dot + span")!.getBoundingClientRect();
    const paragraph = body.querySelector("p")!;
    const range = document.createRange(); range.selectNodeContents(paragraph);
    return { historyTop: history.getBoundingClientRect().top, historyBottom: history.getBoundingClientRect().bottom,
      top: element.getBoundingClientRect().top, bottom: element.getBoundingClientRect().bottom,
      headingY: heading.top, headY: head.top, versionY: version.top, bodyY: range.getClientRects()[0]!.top,
      background: getComputedStyle(history).backgroundColor, sunk: getComputedStyle(document.documentElement).getPropertyValue("--sunk").trim(),
      fontSize: parseFloat(getComputedStyle(body).fontSize), chars: body.clientWidth / parseFloat(getComputedStyle(body).fontSize) };
  });
  await mkdir(captures, { recursive: true });
  await writeFile(`${captures}/${language}-goal-geometry.json`, JSON.stringify({ ...geometry, triggerGap: bounds.y - button.y - button.height }, null, 2) + "\n");
  expect(geometry.headingY).toBeCloseTo(geometry.headY, 0);
  expect(Math.abs(geometry.versionY - geometry.bodyY)).toBeLessThanOrEqual(4);
  expect(geometry.historyTop - geometry.top).toBeLessThanOrEqual(1);
  expect(geometry.bottom - geometry.historyBottom).toBeLessThanOrEqual(1);
  expect(geometry.background).toBe(await page.evaluate(color => { const node = document.createElement("span"); node.style.color = color; document.body.append(node); const value = getComputedStyle(node).color; node.remove(); return value; }, geometry.sunk));
  expect(geometry.fontSize).toBe(14.5);
  expect(geometry.chars).toBeGreaterThanOrEqual(32); expect(geometry.chars).toBeLessThanOrEqual(38.1);
  await expect(card.locator('.goal-version[data-current="true"]')).toHaveCount(1);
  await expect(card.locator(".goal-version")).not.toContainText(["当前", "当前", "当前"]);
  await expect(card.locator(".goal-version")).not.toContainText(["正文", "正文", "正文"]);
  await card.locator(".goal-version").nth(1).click();
  await expect(card.locator(".goal-version").nth(1)).toHaveAttribute("aria-pressed", "true");
  await expect(card.locator(".goal-body")).toContainText("第 2 版的正文");
  const selectedBackground = await card.locator(".goal-version").nth(1).evaluate(node => getComputedStyle(node).backgroundColor);
  expect(selectedBackground).not.toBe(geometry.background);
  await noHint(page, card.locator(".goal-close"));
  if (language === "zh") await shot(page, "zh-dark-1280-goal-history");
  await page.keyboard.press("Escape");
  await expect(card).toBeHidden();
  await expect(trigger).toBeFocused();
  await trigger.blur();

  const copy = header.locator('[data-slot="copyable-identifier"]');
  await noHint(page, copy);
  expect(await copy.locator(":scope > span").evaluate(node => node.scrollWidth <= node.clientWidth + 1), "the middle-shortened ID still shows its tail").toBe(true);
  await copy.click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(macro);
  await expect(copy.locator(".lucide-check")).toBeVisible();
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await expect(copy.locator(".lucide-copy")).toBeVisible({ timeout: 3000 });

  const filter = page.locator('[data-slot="timeline-row-filter"]');
  const toggle = filter.getByRole("button");
  const total = await page.locator('[data-slot="timeline-row-name"]').count();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect(filter.locator('[data-slot="timeline-row-count"]')).toHaveText(String(total));
  if (language === "zh") await shot(page, "zh-dark-1280-filter-all");
  const off = await toggle.evaluate(node => getComputedStyle(node).backgroundColor);
  await toggle.click(); await page.mouse.move(0, 0); await settle(page);
  const shown = await page.locator('[data-slot="timeline-row-name"]').count();
  await expect(filter.locator('[data-slot="timeline-row-count"]')).toHaveText(`${shown} / ${total}`);
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  expect(await toggle.evaluate(node => getComputedStyle(node).backgroundColor)).not.toBe(off);
  if (language === "zh") await shot(page, "zh-dark-1280-filter-unfinished");
});

test("one goal revision has no history column", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await setup(page, "zh", false, true);
  await page.locator('[data-slot="macro-goal-trigger"]').click();
  const card = page.locator('[data-slot="macro-goal-card"]');
  await expect(card.locator('[data-slot="macro-goal-scroll"]')).toHaveAttribute("data-history", "false");
  await expect(card.getByRole("navigation")).toHaveCount(0);
  await shot(page, "zh-dark-1280-goal-single");
});

for (const reduced of [false, true]) test(`shared titles scroll by pointer and keyboard; reduced=${reduced}`, async ({ page }) => {
  await page.emulateMedia({ reducedMotion: reduced ? "reduce" : "no-preference" });
  await setup(page, "zh", true);
  const targets = [page.locator('[data-slot="macro-overview-header"] h2'),
    page.locator('[data-slot="macro-group-heading"] [data-slot="scrolling-title"]').first(),
    page.locator('[data-slot="macro-entry-title"] [data-slot="scrolling-title"]').first(),
    page.locator('[data-slot="timeline-row-name"] [data-slot="scrolling-title"]').first()];
  if (!reduced) await shot(page, "zh-dark-1280-long-title-still");
  for (const [index, target] of targets.entries()) {
    await expect(target).toHaveAttribute("data-overflow", "true");
    await expect(target).toHaveCSS("mask-image", /linear-gradient/);
    await target.hover();
    const text = target.locator('[data-slot="scrolling-title-text"]');
    if (reduced) {
      expect(await text.evaluate(node => node.getAnimations().length)).toBe(0);
      await expect(text).toHaveCSS("transform", "none");
    } else {
      await expect.poll(() => text.evaluate(node => node.getAnimations().length)).toBe(1);
      await settle(page);
      expect(await text.evaluate(node => node.getAnimations().length)).toBe(1);
      if (index === 0) {
        await text.evaluate(node => { const transition = node.getAnimations()[0]!; transition.pause(); transition.currentTime = Number(transition.effect!.getComputedTiming().duration) / 2; });
        await shot(page, "zh-dark-1280-long-title-scrolling");
      }
      const movement = await text.evaluate(node => {
        const animation = node.getAnimations()[0]!; animation.pause();
        const at = (ms: number) => { animation.currentTime = ms; return new DOMMatrix(getComputedStyle(node).transform).m41; };
        const start = at(0), middle = at(500), next = at(1000);
        const duration = Number(animation.effect!.getComputedTiming().duration);
        const end = at(duration), rest = at(duration * 2 + 2000);
        const viewport = node.parentElement!.getBoundingClientRect();
        const range = document.createRange();
        range.setStart(node.firstChild!, node.textContent!.length - 1); range.setEnd(node.firstChild!, node.textContent!.length);
        const glyph = range.getBoundingClientRect();
        return { start, middle, next, end, rest, sampledAfterMs: duration * 2 + 2000, durationMs: duration, lastGlyphRightInset: viewport.right - glyph.right, lastGlyphLeftInset: glyph.left - viewport.left };
      });
      await mkdir(captures, { recursive: true });
      await writeFile(`${captures}/title-movement-${index}.json`, JSON.stringify(movement, null, 2) + "\n");
      expect(movement.start).toBeCloseTo(0, 0); expect(movement.middle).toBeCloseTo(-20, 0); expect(movement.next).toBeCloseTo(-40, 0);
      expect(movement.rest).toBeCloseTo(movement.end, 0);
      expect(movement.lastGlyphRightInset, "the last glyph clears the right fade at the end").toBeGreaterThanOrEqual(27.5);
      expect(movement.lastGlyphLeftInset).toBeGreaterThanOrEqual(27.5);
      expect(await target.evaluate(node => getComputedStyle(node).maskImage)).toMatch(/^linear-gradient\((?:to right|90deg), rgba\(0, 0, 0, 0\)/);
      if (index === 0) await shot(page, "zh-dark-1280-title-at-end");
    }
    await page.mouse.move(0, 0);
    if (!reduced) await expect(text).toHaveCSS("transition-duration", "0.2s");
    await expect(text).toHaveCSS("transform", "none");
    await page.keyboard.press("Tab");
    const focus = target.locator("xpath=ancestor-or-self::*[self::button or self::h2][1]");
    await focus.focus();
    expect(await text.evaluate(node => node.getAnimations().length)).toBe(reduced ? 0 : 1);
    await focus.blur();
    await expect(text).toHaveCSS("transform", "none");
    await expect(page.getByRole("tooltip")).toHaveCount(0);
  }
  if (!reduced) await shot(page, "zh-dark-1280-sidebar");
  const first = page.locator('[data-slot="timeline-row-name"]').first();
  const runId = await first.getAttribute("data-run-id");
  await page.route(`**/api/tasks/${runId}`, async route => {
    const response = await route.fetch(), data = await response.json(); data.workflow.title = longTitle;
    await route.fulfill({ response, json: data });
  });
  await first.click();
  const detail = page.locator('[data-slot="detail-header"]');
  const title = detail.locator("h2");
  await expect(title).toHaveAttribute("data-overflow", "true");
  await title.hover();
  expect(await title.locator("span").evaluate(node => node.getAnimations().length)).toBe(reduced ? 0 : 1);
  await expect(detail.getByText("开始", { exact: true })).toHaveCount(0);
  await noHint(page, detail.locator('[data-slot="detail-back"]'));
  await noHint(page, detail.locator('[data-slot="detail-refresh"]'));
});

test("removed macro controls and time-label hints stay absent", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await setup(page, "zh");
  const list = page.locator('[data-slot="macro-list"]');
  await noHint(page, list.locator('[data-slot="macro-fold"]'));
  for (const tab of await list.getByRole("tab").all()) await noHint(page, tab);
  const group = list.locator('[data-slot="macro-group"]').first();
  await group.locator('[data-slot="macro-group-heading"]').hover();
  await noHint(page, group.locator('[data-slot="macro-sort-button"]'));
  const selected = list.locator(`[data-macro-task-id="${macro}"]`);
  await noHint(page, selected.locator('[data-slot="macro-menu-button"]'));
  await noHint(page, selected.locator('[data-slot="state-mark"]'));
  const header = page.locator('[data-slot="macro-overview-header"]');
  await header.getByRole("button", { name: "时间统计", exact: true }).click();
  const stats = page.locator('[data-slot="time-stats-values"]');
  await expect(stats.locator("dt")).toHaveText(["从开始到最近活动", "有工作在运行的时间", "各回合相加"]);
  for (const label of await stats.locator("dt").all()) await noHint(page, label);
});
