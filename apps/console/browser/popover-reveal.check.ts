import { expect, type Locator, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, settle, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const captures = fileURLToPath(new URL("../../../tmp/reveal/", import.meta.url));

async function freezeReveals(page: Page) {
  await page.evaluate(() => {
    document.addEventListener("transitionrun", event => {
      const target = event.target as Element;
      if (!target.matches(".popover-reveal")) return;
      for (const animation of target.getAnimations()) if (animation instanceof CSSTransition && animation.transitionProperty === event.propertyName) {
        animation.pause(); animation.currentTime = 0;
      }
    }, true);
    document.addEventListener("animationstart", event => {
      const target = event.target as Element;
      if (!target.matches(".popover-reveal")) return;
      for (const animation of target.getAnimations()) if (animation instanceof CSSAnimation && animation.animationName === event.animationName) {
        animation.pause(); animation.currentTime = 0;
      }
    }, true);
  });
}

async function motionEvents(page: Page) {
  // CSS start events arrive on a rendering frame; drain them before seeking the paused motion.
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

async function frame(card: Locator, milliseconds: number) {
  return card.evaluate((element, time) => {
    for (const animation of element.getAnimations()) { animation.pause(); animation.currentTime = time; }
    const style = getComputedStyle(element), line = getComputedStyle(element, "::after");
    const box = element.getBoundingClientRect();
    const lineTop = box.top + parseFloat(style.borderTopWidth) + parseFloat(line.top);
    const lineLeft = box.left + parseFloat(style.borderLeftWidth) + parseFloat(line.left);
    const inset = style.clipPath.slice(6, -1).split(" round ")[0]!.match(/calc\([^)]*\)|[^\s]+/g)!;
    const revealed = element.getAttribute("data-side") === "bottom"
      ? { top: box.top, bottom: lineTop }
      : { top: lineTop + 2, bottom: box.bottom };
    // Check the first glyph of every visible text node, including headings and list items.
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const glyphs: boolean[] = [];
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      if (text.parentElement!.closest("svg")) continue;
      const first = text.textContent!.search(/\S/);
      if (first < 0) continue;
      const range = document.createRange(); range.setStart(text, first); range.setEnd(text, first + 1);
      const glyph = range.getBoundingClientRect();
      if (glyph.width && glyph.top >= revealed.top + 1 && glyph.bottom < revealed.bottom - 1) {
        glyphs.push(text.parentElement!.contains(document.elementFromPoint(glyph.x + glyph.width / 2, glyph.y + glyph.height / 2)));
      }
    }
    return { progress: Number(style.getPropertyValue("--reveal-progress")), inset, glyphs,
      line: { left: lineLeft, right: lineLeft + parseFloat(line.width), top: lineTop, bottom: lineTop + 2 },
      side: element.getAttribute("data-side"), duration: style.transitionDuration, easing: style.transitionTimingFunction,
      colour: line.backgroundColor, token: style.getPropertyValue(element.classList.contains("goal-card") ? "--goal" : "--ink2").trim() };
  }, milliseconds);
}

async function checkReveal(page: Page, trigger: Locator, card: Locator, name: string, theme: string) {
  await freezeReveals(page);
  await trigger.click();
  await expect(card).toHaveAttribute("data-reveal-ready", "true");
  await motionEvents(page);
  if (name === "help") {
    await expect(card.locator(":scope > p")).toHaveCount(2);
    await expect(card.locator(":scope > p").first()).toContainText("发给 Router 的模型提供方");
  }
  const anchor = (await trigger.boundingBox())!;
  const start = await frame(card, 0);
  expect(start.progress).toBe(0);
  expect(start.duration).toBe("0.2s"); expect(start.easing).toBe("ease-out");
  expect(Math.abs(start.line.left - anchor.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(start.line.right - anchor.x - anchor.width)).toBeLessThanOrEqual(1);
  expect(Math.abs(start.side === "bottom" ? start.line.top - anchor.y - anchor.height : start.line.bottom - anchor.y)).toBeLessThanOrEqual(1);
  const middle = await frame(card, 100);
  expect(middle.progress).toBeGreaterThan(0); expect(middle.progress).toBeLessThan(1);
  expect(middle.inset[1], "the right edge is never clipped").toBe("0px");
  expect(middle.inset[3] ?? middle.inset[1], "the left edge is never clipped").toBe("0px");
  expect(middle.glyphs.length, "some text has been revealed at the midpoint").toBeGreaterThan(0);
  expect(middle.glyphs.every(Boolean), "the beginnings of revealed text remain visible").toBe(true);
  const tokenColour = await card.evaluate((element, token) => {
    const probe = document.createElement("span"); probe.style.color = token; element.append(probe);
    const colour = getComputedStyle(probe).color; probe.remove(); return colour;
  }, middle.token);
  expect(middle.colour).toBe(tokenColour);
  if (theme === "dark" && ["goal", "domain", "help", "legend"].includes(name)) {
    await mkdir(captures, { recursive: true });
    await writeFile(`${captures}/${name}-midpoint.json`, JSON.stringify({ start, middle, anchor,
      observed: await card.evaluate(element => ({ box: element.getBoundingClientRect().toJSON(), side: element.getAttribute("data-side"), progress: getComputedStyle(element).getPropertyValue("--reveal-progress") })) }, null, 2));
    await page.screenshot({ path: `${captures}/${name}-dark-1440-midpoint.png` });
  }
  await frame(card, 200);
  // Closing remeasures against the actual trigger and reverses the same reveal.
  await page.keyboard.press("Escape");
  await expect(card).toHaveAttribute("data-state", "closed");
  await motionEvents(page);
  const closing = await frame(card, 100);
  expect(closing.progress).toBeGreaterThan(0); expect(closing.progress).toBeLessThan(1);
  const end = await frame(card, 200);
  expect(end.progress).toBe(0);
  expect(Math.abs(end.line.left - anchor.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(end.line.right - anchor.x - anchor.width)).toBeLessThanOrEqual(1);
  await expect(card).toHaveCount(0);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await trigger.click();
  await expect(card).toHaveAttribute("data-reveal-ready", "true");
  expect(await card.evaluate(element => ({ animation: getComputedStyle(element).animationName, progress: Number(getComputedStyle(element).getPropertyValue("--reveal-progress")), animations: element.getAnimations().length })))
    .toEqual({ animation: "none", progress: 1, animations: 0 });
  await page.keyboard.press("Escape");
  await expect(card).toHaveCount(0);
  return start.side;
}

for (const theme of ["dark", "light"]) test.describe(`anchored card reveals ${theme}`, () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 1000 }, reducedMotion: "no-preference" });
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
  });
  test("goal", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    await checkReveal(page, page.locator('[data-slot="macro-goal-trigger"]'), page.locator('[data-slot="macro-goal-card"]'), "goal", theme);
  });
  test("time statistics", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    await checkReveal(page, page.getByRole("button", { name: "时间统计", exact: true }), page.locator('[data-slot="popover-content"]:has([data-slot="time-stats-body"])'), "time", theme);
  });
  test("session coverage", async ({ page }) => {
    await open(page, `#tasks/${macro}/preview-run-a1/7?tab=session`);
    await checkReveal(page, page.getByRole("button", { name: "会话内容", exact: true }), page.locator('[data-slot="popover-content"]:has([data-slot="session-coverage"])'), "session", theme);
  });
  test("legend", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    await checkReveal(page, page.locator('[data-slot="legend-trigger"]'), page.locator('[data-slot="timeline-legend"]'), "legend", theme);
  });
  test("marker cluster", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    await checkReveal(page, page.locator('[data-slot="event-cluster"]').first(), page.locator('[data-slot="popover-content"]'), "cluster", theme);
  });
  test("settings question mark measures the control inside its label anchor", async ({ page }) => {
    await open(page, "#buddies/router/routing");
    await checkReveal(page, page.locator('[data-slot="router-setting-help"]').first(), page.locator('[data-slot="popover-content"]'), "help", theme);
  });
  test("domain details", async ({ page }) => {
    await open(page, "#buddies/router/maintenance");
    const row = page.locator('[data-slot="domain-row"]').first();
    await row.hover();
    await checkReveal(page, row.locator('.domain-tree-info'), page.locator('[data-slot="domain-details"]'), "domain", theme);
  });
  test("reverses an interrupted goal reveal without jumping to an endpoint", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    await freezeReveals(page);
    const trigger = page.locator('[data-slot="macro-goal-trigger"]');
    const card = page.locator('[data-slot="macro-goal-card"]');
    await trigger.click();
    await expect(card).toHaveAttribute("data-reveal-ready", "true");
    await motionEvents(page);
    const opening = await frame(card, 100);
    await page.keyboard.press("Escape");
    await expect(card).toHaveAttribute("data-state", "closed");
    await motionEvents(page);
    expect((await frame(card, 0)).progress).toBeCloseTo(opening.progress, 4);
    const closing = await frame(card, 50);
    expect(closing.progress).toBeGreaterThan(0); expect(closing.progress).toBeLessThan(opening.progress);
    await trigger.click();
    await expect(card).toHaveAttribute("data-state", "open");
    await motionEvents(page);
    expect((await frame(card, 0)).progress).toBeCloseTo(closing.progress, 4);
  });
  test("toolbar tooltip stays 16px from the viewport edge", async ({ page }) => {
    await open(page, "#buddies/router/routing");
    await page.getByRole("navigation", { name: "Router 的工作" }).getByRole("link", { name: /^路由/ }).hover();
    const tooltip = page.locator('[data-slot="tooltip-content"]');
    await expect(tooltip).toBeVisible();
    await settle(page);
    const bounds = (await tooltip.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(16);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(1440 - 16);
  });
  test("legend flips upward in a short viewport", async ({ page }) => {
    await page.setViewportSize({ width: 800, height: 500 });
    await open(page, `#tasks/${macro}`);
    await settle(page);
    expect(await checkReveal(page, page.locator('[data-slot="legend-trigger"]'), page.locator('[data-slot="timeline-legend"]'), "flipped-legend", theme)).toBe("top");
  });
});
