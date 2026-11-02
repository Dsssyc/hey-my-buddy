import { expect, type Locator } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const captures = fileURLToPath(new URL("../../../tmp/console-goal-card", import.meta.url));
const body = "让黑板靠 Git 里的项目标记认仓库，路径只用来找到它；宏任务有一份可修改、留历史的目标说明；没有任务的空壳在删除失败或中断之后仍能证明归属。\n\n### 做到\n\n- 三项随 0.59.0 一次升级装上，日常黑板的存量都转换过来\n- 搬家、拷贝与新 clone 的情形都有真实 Git 的测试\n\n### 不做\n\n- 没有 Git 的文件夹的入口\n- 把位置记成第二份身份";
const goals = [
  { body, revision: 3, createdAt: "2026-11-01T06:02:00Z" },
  { body: "让黑板靠 Git 里的项目标记认仓库。\n\n第二版的完整说明。", revision: 2, createdAt: "2026-11-01T02:31:00Z" },
  { body: "下一个存储批：有 Git 时仓库靠标记认。\n\n第一版的完整说明。", revision: 1, createdAt: "2026-10-31T19:41:00Z" },
];

async function expectHistoryPadding(card: Locator) {
  const padding = await card.locator('.goal-version[aria-pressed="true"]').evaluate(element => {
    const row = element.getBoundingClientRect();
    const dot = element.querySelector(".goal-version-dot")!.getBoundingClientRect();
    const text = element.querySelector(".goal-version-dot + span")!.getBoundingClientRect();
    return {
      dotInside: dot.left >= row.left && dot.right <= row.right && dot.top >= row.top && dot.bottom <= row.bottom,
      dotLeft: dot.left - row.left,
      textTop: text.top - row.top,
    };
  });
  expect(padding.dotInside, "the selected version's background contains its dot").toBe(true);
  expect(padding.dotLeft, "the selected version's dot has a left inset").toBeGreaterThanOrEqual(8);
  expect(padding.textTop, "the selected version's text has a top inset").toBeGreaterThanOrEqual(6);
}

for (const scene of [
  { language: "zh", theme: "dark", width: 1280 },
  { language: "zh", theme: "light", width: 1280 },
  { language: "en", theme: "dark", width: 390 },
]) test(`goal sheet and captures ${scene.language}-${scene.theme}-${scene.width}`, async ({ page }) => {
  const name = `${scene.language}-${scene.theme}-${scene.width}`;
  await page.setViewportSize({ width: scene.width, height: 850 });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.addInitScript(scene => {
    localStorage.setItem("hey-my-buddy.console.theme", scene.theme);
    localStorage.setItem("hey-my-buddy.language", scene.language);
  }, scene);
  let reads = 0;
  await page.route(`**/api/macro-tasks/${macro}/timeline?*`, async route => {
    const response = await route.fetch(); const data = await response.json();
    data.macroTask.goal = goals[0];
    data.macroTask.title = "存储批：仓库身份、目标说明、空壳归属";
    await route.fulfill({ response, json: data });
  });
  await page.route(`**/api/macro-tasks/${macro}/goals`, async route => {
    reads++; await route.fulfill({ json: { macroTaskId: macro, goals } });
  });
  await open(page, `#tasks/${macro}`);
  const trigger = page.locator('[data-slot="macro-goal-trigger"]');
  await expect(trigger).toBeVisible();
  await expect(trigger).toHaveCSS("text-overflow", "ellipsis");
  const header = page.locator('[data-slot="macro-overview-header"]');
  const facts = header.locator('[data-slot="macro-overview-facts"]');
  await expect(facts.locator("dt")).toHaveText(scene.language === "zh" ? ["项目", "Host", "开始", "历时", "编号", "目标"] : ["Project", "Host", "Started", "Elapsed", "ID", "Goal"]);
  expect(reads).toBe(0);
  await mkdir(captures, { recursive: true });
  await page.screenshot({ path: `${captures}/${name}-closed.png` });
  const before = await header.boundingBox();
  await trigger.click();
  const card = page.locator('[data-slot="macro-goal-card"]');
  await expect(card).toBeFocused();
  await expect(card.locator("strong, h3").first()).toBeVisible();
  // Pause the actual CSS opening animation at its midpoint, without changing CSS.
  await card.evaluate(element => {
    for (const animation of element.getAnimations()) { animation.pause(); animation.currentTime = 100; }
  });
  await page.screenshot({ path: `${captures}/${name}-mid.png` });
  const middle = await card.evaluate(element => ({ progress: Number(getComputedStyle(element).getPropertyValue("--goal-progress")), clip: getComputedStyle(element).clipPath }));
  expect(middle.progress).toBeGreaterThan(0); expect(middle.progress).toBeLessThan(1);
  // Only the bottom of the full-width sheet is revealed; the green line alone expands sideways.
  const insets = middle.clip.slice("inset(".length, -1).split(" round ")[0]!.match(/calc\([^)]*\)|[^\s]+/g)!;
  expect(insets[1], "the sheet's right edge is never clipped").toBe("0px");
  expect(insets[3] ?? insets[1], "the sheet's left edge is never clipped").toBe("0px");
  expect(await card.evaluate(element => {
    const paragraph = element.querySelector('[data-slot="macro-goal-body"] p')!;
    const range = document.createRange(); range.setStart(paragraph.firstChild!, 0); range.setEnd(paragraph.firstChild!, 1);
    const glyph = range.getBoundingClientRect();
    return paragraph.contains(document.elementFromPoint(glyph.x + glyph.width / 2, glyph.y + glyph.height / 2));
  }), "the first character of the goal remains visible mid-animation").toBe(true);
  await card.evaluate(element => { for (const animation of element.getAnimations()) animation.finish(); });
  await expectHistoryPadding(card);
  const typography = await card.evaluate(element => {
    const frame = element.getBoundingClientRect();
    const body = element.querySelector('[data-slot="macro-goal-body"]')!;
    const bounds = body.getBoundingClientRect();
    const padding = getComputedStyle(element.querySelector('[data-slot="macro-goal-scroll"]')!);
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    const textOutside: string[] = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent?.trim()) continue;
      const range = document.createRange(); range.selectNodeContents(node);
      if ([...range.getClientRects()].some(rect => rect.width > 0 && (rect.left < bounds.left - 1 || rect.right > bounds.right + 1))) textOutside.push(node.textContent);
    }
    return {
      leftPadding: padding.paddingLeft, rightPadding: padding.paddingRight,
      inset: bounds.left - frame.left,
      textOutside,
      lists: [...body.querySelectorAll("ul,ol")].map(list => ({
        position: getComputedStyle(list).listStylePosition,
        inside: [...list.querySelectorAll("li")].every(item => {
          const rect = item.getBoundingClientRect(); return rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1;
        }),
      })),
    };
  });
  expect(typography.leftPadding).toBe("24px"); expect(typography.rightPadding).toBe("24px");
  expect(typography.inset).toBeGreaterThanOrEqual(24);
  expect(typography.textOutside, "all goal text stays within the prose column").toEqual([]);
  expect(typography.lists.length).toBeGreaterThan(0);
  expect(typography.lists.every(list => list.position === "inside" && list.inside), "list markers and text stay within the 24px prose inset").toBe(true);
  await page.screenshot({ path: `${captures}/${name}-open.png` });
  expect(await header.boundingBox()).toEqual(before);
  const box = (await card.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(scene.width);
  expect(box.y + box.height).toBeLessThanOrEqual(850);
  expect(await card.evaluate(element => getComputedStyle(element).borderRadius)).toBe("6px");
  expect(await card.locator('[data-current="true"]').count()).toBe(1);
  await card.locator(".goal-version").nth(1).click();
  await expect(card.locator('[data-slot="macro-goal-body"]')).toContainText("第二版的完整说明。");
  await expectHistoryPadding(card);
  await page.keyboard.press("Escape");
  await expect(card).toBeHidden(); await expect(trigger).toBeFocused();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await trigger.click();
  await expect(card).toBeVisible();
  expect(await card.evaluate(element => getComputedStyle(element).animationName)).toBe("none");
  await page.locator('[data-slot="macro-overview-header"] h2').first().click();
  await expect(card).toBeHidden();
  // On narrow screens the explicit list button opens the same list in place.
  if (scene.width < 760) await page.getByRole("button", { name: "‹ Macro tasks", exact: true }).click();
  const list = page.locator('[data-slot="macro-list"]:visible');
  await expect(list).toBeVisible();
  const rows = list.locator('[data-slot="macro-entry"]:visible');
  for (const row of await rows.all()) {
    expect((await row.boundingBox())!.height).toBe(32);
    expect(await row.locator("time").count()).toBe(0);
  }
  await page.screenshot({ path: `${captures}/${name}-list.png` });
});

test("reads the synthetic preview's real goal endpoint and confines long content scrolling to the card", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await open(page, `#tasks/${macro}`);
  const trigger = page.locator('[data-slot="macro-goal-trigger"]');
  const response = page.waitForResponse(response => response.url().endsWith(`/api/macro-tasks/${macro}/goals`));
  await trigger.click();
  expect((await response).ok()).toBe(true);
  const card = page.locator('[data-slot="macro-goal-card"]');
  await expect(card.locator(".goal-version")).toHaveCount(1);
  await expectHistoryPadding(card);
  await page.keyboard.press("Escape");
  await page.route(`**/api/macro-tasks/${macro}/goals`, route => route.fulfill({ json: { macroTaskId: macro, goals: [{ ...goals[0], revision: 1, body: body.repeat(30) }] } }));
  await trigger.click();
  const scroll = card.locator('[data-slot="macro-goal-scroll"]');
  await expect(scroll.locator("h3").first()).toBeVisible();
  await expect.poll(() => scroll.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight)).toBe(true);
});

test("keeps the Chinese zoom hint on one line at 1280px and wraps only when its viewport is too narrow", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 850 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    localStorage.setItem("hey-my-buddy.console.theme", "dark");
    localStorage.setItem("hey-my-buddy.language", "zh");
  });
  await open(page, `#tasks/${macro}`);
  const ruler = page.locator('[data-slot="zoom-ruler"]:visible');
  const bounds = (await ruler.boundingBox())!;
  await page.mouse.move(bounds.x + bounds.width - 80, bounds.y + 10);
  const tip = page.locator('[data-slot="tooltip-content"]').filter({ hasText: "滚轮缩放" });
  await expect(tip).toBeVisible();
  const lines = await tip.evaluate(element => {
    const text = document.createRange(); text.selectNodeContents(element.firstChild!);
    return [...text.getClientRects()].filter(rect => rect.width > 0).length;
  });
  expect(lines).toBe(1);
  await mkdir(captures, { recursive: true });
  await page.screenshot({ path: `${captures}/zh-dark-1280-zoom-hint.png` });
  const box = (await tip.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(1280);
  await page.mouse.move(0, 0);
  await expect(tip).toBeHidden();
  await page.setViewportSize({ width: 390, height: 850 });
  const resized = (await ruler.boundingBox())!;
  const viewport = (await page.locator('[data-slot="timeline-tracks"]').boundingBox())!;
  await page.mouse.move(viewport.x + viewport.width - 30, resized.y + 10);
  await expect(tip).toBeVisible();
  const narrow = (await tip.boundingBox())!;
  expect(narrow.x).toBeGreaterThanOrEqual(0); expect(narrow.x + narrow.width).toBeLessThanOrEqual(390);
});


test("keeps the information row fixed while identifier copy feedback replaces the value", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.setViewportSize({ width: 1280, height: 850 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => localStorage.setItem("hey-my-buddy.language", "zh"));
  await open(page, `#tasks/${macro}`);
  const facts = page.locator('[data-slot="macro-overview-facts"]');
  const before = await facts.locator(":scope > div").evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()));
  const copy = facts.getByRole("button", { name: `复制标识 ${macro}`, exact: true });
  await copy.click();
  await expect(copy.locator('[aria-live="polite"]')).toHaveText("已复制");
  expect(await facts.locator(":scope > div").evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()))).toEqual(before);
  await expect(copy.locator('[aria-live="polite"]')).toBeEmpty();
  expect(await copy.innerText()).toContain("macro-");
});


test("captures the shared Streamdown typography in a Chinese dark session", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 850 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    localStorage.setItem("hey-my-buddy.console.theme", "dark");
    localStorage.setItem("hey-my-buddy.language", "zh");
  });
  await open(page, "#tasks/macro-22222222-2222-4222-8222-222222222222/preview-run-b1/5?tab=session");
  const answer = page.locator('[data-session-row="answer"]').filter({ hasText: "路径编码一致" });
  await expect(answer).toBeVisible();
  const heading = answer.getByRole("heading", { name: "核对结果" });
  await expect(heading).toHaveCSS("font-size", "24px");
  await expect(answer.locator("ul")).toHaveCSS("list-style-position", "inside");
  await answer.scrollIntoViewIfNeeded();
  await mkdir(captures, { recursive: true });
  await page.screenshot({ path: `${captures}/zh-dark-1280-session-markdown.png` });
  const facts = await answer.evaluate(element => ({
    heading: { size: getComputedStyle(element.querySelector("h2")!).fontSize, lineHeight: getComputedStyle(element.querySelector("h2")!).lineHeight },
    list: { position: getComputedStyle(element.querySelector("ul")!).listStylePosition, type: getComputedStyle(element.querySelector("ul")!).listStyleType },
    itemPadding: getComputedStyle(element.querySelector("li")!).paddingTop,
    breaks: element.querySelectorAll("br").length,
  }));
  await writeFile(`${captures}/session-markdown-typography.json`, JSON.stringify(facts, null, 2) + "\n");
});
