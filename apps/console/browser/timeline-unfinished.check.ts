import { expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, test, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart } from "./preview";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { createTimelineScale, timelineScaleInput } from "../src/timeline/scale";
import { TIMELINE_OUTSET_PX } from "../src/timeline/trackMarks";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const other = "macro-22222222-2222-4222-8222-222222222222";
const captures = fileURLToPath(new URL("../../../tmp/console-header/unfinished", import.meta.url));
const names = (page: Page) => page.locator('[data-slot=timeline-row-name]');
const header = (page: Page) => page.locator('[data-slot=timeline-row-filter]');
const canvas = (page: Page) => page.locator('[data-slot=timeline-canvas]');
const ppm = (page: Page) => canvas(page).getAttribute("data-ppm").then(Number);

for (const scene of [
  { locale: "zh-CN", language: "zh", theme: "dark", width: 1440 },
  { locale: "zh-CN", language: "zh", theme: "light", width: 1440 },
  { locale: "en-US", language: "en", theme: "dark", width: 1440 },
  { locale: "zh-CN", language: "zh", theme: "dark", width: 390 },
]) test.describe(`unfinished timeline ${scene.language}-${scene.theme}-${scene.width}`, () => {
  test.use({ locale: scene.locale, viewport: { width: scene.width, height: 1000 }, reducedMotion: "reduce" });
  test("filters complete rows, fits their time range, persists and keeps the switch clear of the zoom hint", async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem("hey-my-buddy.console.theme", theme), scene.theme);
    await page.clock.setFixedTime(new Date("2026-09-27T10:00:00Z"));
    let timeline!: MacroTaskTimeline;
    let otherTimeline!: MacroTaskTimeline;
    await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
      const response = await route.fetch(); timeline = await response.json();
      await route.fulfill({ response, headers: { ...response.headers(), date: new Date(timeline.observedAt).toUTCString() }, json: timeline });
    });
    await page.route(`**/api/macro-tasks/${other}/timeline*`, async route => {
      const response = await route.fetch(); otherTimeline = await response.json();
      await route.fulfill({ response, json: otherTimeline });
    });
    await open(page, `#tasks/${macro}`);
    const unfinished = header(page).getByRole("button", { name: scene.language === "zh" ? "只看未结束的" : "Unfinished only", exact: true });
    const count = header(page).locator('[data-slot=timeline-row-count]');
    await expect(unfinished).toHaveAttribute("aria-pressed", "false");
    await expect(names(page)).toHaveCount(timeline.rows.length);
    await expect(count).toHaveText(String(timeline.rows.length));
    await expectTextUncut(header(page).locator("button, :scope > span"));
    await expectWholeAndApart(header(page), header(page).locator(":scope > span, :scope > div"));
    if (scene.width === 1440) await expect(page.locator('[data-slot=macro-entry][aria-pressed=true]')).toBeVisible();
    await mkdir(captures, { recursive: true });
    const shot = `${captures}/${scene.language}-${scene.theme}-${scene.width}`;
    await page.mouse.move(0, 0);
    await page.screenshot({ path: `${shot}-all.png` });

    const ruler = page.locator('[data-slot=zoom-ruler]');
    const hint = page.locator('[data-slot=tooltip-content]').filter({ hasText: scene.language === "zh" ? "滚轮缩放" : "Shift" });
    await ruler.hover({ position: { x: scene.width === 390 ? 80 : 400, y: 15 } });
    await expect(hint).toBeVisible();
    await unfinished.hover();
    await expect(hint).toBeHidden();
    await page.waitForTimeout(700);
    await expect(hint).toBeHidden();
    await ruler.press("+"); await ruler.press("+");
    await unfinished.focus(); await unfinished.press("Enter");
    await expect(unfinished).toHaveAttribute("aria-pressed", "true");
    await expect(hint).toBeHidden();
    const shown = timeline.rows.filter(row => ["active", "host", "review"].includes(row.category));
    const ids = shown.map(row => row.runId);
    expect(new Set(shown.map(row => row.category))).toEqual(new Set(["active", "host", "review"]));
    expect(shown.some(row => row.kind === "helper")).toBe(true);
    await expect.poll(() => names(page).evaluateAll(nodes => nodes.map(node => node.getAttribute("data-run-id")))).toEqual(ids);
    await expect(count).toHaveText(`${ids.length} / ${timeline.rows.length}`);
    const filtered = { ...timeline, rows: shown, spans: timeline.spans.filter(span => ids.includes(span.runId)), events: timeline.events.filter(event => ids.includes(event.runId)) };
    const available = await page.locator('[data-slot=timeline-tracks]').evaluate(node => node.clientWidth)
      - (scene.width === 390 ? 0 : await header(page).evaluate(node => node.getBoundingClientRect().width)) - TIMELINE_OUTSET_PX * 2;
    const input = timelineScaleInput(filtered);
    expect(input.domain!.startMs).toBeGreaterThan(timelineScaleInput(timeline).domain!.startMs);
    const expected = createTimelineScale(input, available).pixelsPerMinute;
    expect(await ppm(page)).toBeCloseTo(expected, 5);
    expect(await page.locator('[data-slot=timeline-tracks]').evaluate(node => node.scrollLeft)).toBe(0);
    const status = page.locator('[data-slot=timeline-status] [role=status]');
    await page.mouse.move(0, 0); await unfinished.blur();
    await expect(status).toContainText(new Intl.DateTimeFormat(scene.locale, { hour: "2-digit", minute: "2-digit", hour12: false }).format(input.domain!.startMs));
    await page.screenshot({ path: `${shot}-unfinished.png` });
    await ruler.press("+");
    expect(await ppm(page)).toBeGreaterThan(expected);
    await page.locator('[data-slot=timeline-tracks]').evaluate(node => node.scrollLeft = 100);
    await expectWholeAndApart(header(page), header(page).locator(":scope > span, :scope > div"));
    await page.reload();
    await expect(unfinished).toHaveAttribute("aria-pressed", "true");
    await expect(count).toHaveText(`${ids.length} / ${timeline.rows.length}`);
    await expect(names(page)).toHaveCount(ids.length);
    await page.goto(`#tasks/${other}`);
    await expect(unfinished).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => otherTimeline?.rows.length).toBeGreaterThan(0);
    const otherIds = otherTimeline.rows.filter(row => ["active", "host", "review"].includes(row.category)).map(row => row.runId).sort();
    await expect.poll(() => names(page).evaluateAll(nodes => nodes.map(node => node.getAttribute("data-run-id")).sort())).toEqual(otherIds);
    await expect(count).toHaveText(`${otherIds.length} / ${otherTimeline.rows.length}`);
    await unfinished.focus(); await unfinished.press("Space");
    await expect(unfinished).toHaveAttribute("aria-pressed", "false");
    await expect(names(page)).toHaveCount(otherTimeline.rows.length);
    if (scene.language === "en") {
      await page.locator('[data-slot=timeline-name-divider]').press("Home");
      await expectTextUncut(header(page).locator("button, :scope > span"));
      await expectWholeAndApart(header(page), header(page).locator(":scope > span, :scope > div"));
    }
    await expectNoSidewaysScroll(page);
  });
});

test("an empty unfinished view keeps a reachable switch back to all", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
    const response = await route.fetch(); const timeline: MacroTaskTimeline = await response.json();
    for (const row of timeline.rows) row.category = "ended";
    await route.fulfill({ response, json: timeline });
  });
  await open(page, `#tasks/${macro}`);
  await header(page).getByRole("button", { name: "Unfinished only", exact: true }).click();
  await expect(header(page).locator('[data-slot=timeline-row-count]')).toHaveText(/^0 \/ \d+$/);
  await expect(page.getByText("No unfinished micro tasks", { exact: true })).toBeVisible();
  await header(page).getByRole("button", { name: "Unfinished only", exact: true }).click();
  expect(await names(page).count()).toBeGreaterThan(0);
});
