import { expect } from "@playwright/test";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const spanId = "execution:att-a3-1";

test.use({ viewport: { width: 1440, height: 1000 }, reducedMotion: "no-preference" });

test("a running narrow bar omits its repeated identity and places a changed identity wholly beside it", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-09-27T10:00:00.000Z"));
  await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
  await open(page, `#tasks/${macro}`);
  await expect(page.locator(`[data-key="span:${spanId}"] [data-slot=execution-buddy]`)).toHaveCount(0);

  let durationMs: number | null = null;
  let frozenTimeline: MacroTaskTimeline | null = null;
  await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
    const response = await route.fetch();
    frozenTimeline ??= await response.json();
    const timeline = structuredClone(frozenTimeline!);
    // The display clock comes from the response Date, whose precision is one
    // second. Freeze it and the fixture body at the same instant on every read.
    timeline.observedAt = new Date(Math.floor(Date.parse(timeline.observedAt) / 1000) * 1000).toISOString();
    const live = timeline.spans.find(span => span.spanId === spanId)!;
    if (durationMs !== null) live.startAt = new Date(Date.parse(timeline.observedAt) - durationMs).toISOString();
    live.reminders = null;
    live.expectedSeconds = null;
    live.inquiries = [];
    const row = timeline.rows.find(row => row.runId === live.runId)!;
    row.buddy = { ...row.buddy!, provider: "another-provider" };
    timeline.events = timeline.events.filter(event => event.runId !== live.runId);
    // Keep the fitted scale independent of this span's length.
    timeline.scopeComplete = false;
    await route.fulfill({ response, json: timeline,
      headers: { ...response.headers(), date: new Date(timeline.observedAt).toUTCString() } });
  });

  const segment = page.locator(`[data-key="span:${spanId}"]`);
  const bar = segment.locator('[data-slot=execution-bar][data-outcome=running]');
  const edge = segment.locator('[data-slot=execution-breath]');
  const icon = segment.locator('[data-slot=execution-buddy]');
  await page.reload();
  const canvas = page.locator("[data-slot=timeline-canvas]");
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const ppm = Number(await canvas.getAttribute("data-ppm"));
  expect(ppm).toBeGreaterThan(0);
  // The real bar remains narrower than the mark; the mark must move as a whole.
  durationMs = 12 / ppm * 60_000;
  await page.reload();
  await expect(bar).toBeVisible();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(Number(await canvas.getAttribute("data-ppm"))).toBeCloseTo(ppm, 2);
  await expect(edge).toBeVisible();
  await expect(icon).toBeVisible();
  await icon.locator("img").evaluate(node => (node as HTMLImageElement).decode());
  await icon.scrollIntoViewIfNeeded();
  const [barBox, iconBox] = await Promise.all([bar.boundingBox(), icon.boundingBox()]);
  expect(barBox!.width).toBeGreaterThan(11.5);
  expect(barBox!.width).toBeLessThan(13.5);
  expect(barBox!.width).toBeLessThan(iconBox!.width);
  expect(iconBox!.x + iconBox!.width <= barBox!.x || iconBox!.x >= barBox!.x + barBox!.width).toBe(true);

  await edge.evaluate(node => { const motion = node.getAnimations()[0]!; motion.pause(); motion.currentTime = 0; });
  const first = await icon.boundingBox();
  const iconIsClear = () => segment.evaluate(group => {
    const bar = group.querySelector<SVGRectElement>("[data-slot=execution-bar]")!.getBoundingClientRect();
    const paper = group.querySelector<SVGRectElement>("[data-slot=execution-buddy-paper]")!;
    const icon = group.querySelector<SVGForeignObjectElement>("[data-slot=execution-buddy]")!;
    const box = paper.getBoundingClientRect(), glyph = icon.getBoundingClientRect();
    return (box.right <= bar.left - 4 || box.left >= bar.right + 4)
      && glyph.left >= box.left && glyph.right <= box.right;
  });
  expect(await iconIsClear(), "the complete mark stays clear of the real bar").toBe(true);
  await edge.evaluate(node => { node.getAnimations()[0]!.currentTime = 1000; });
  expect(await icon.boundingBox(), "the icon never moves with the edge").toEqual(first);
  expect(await iconIsClear(), "the complete mark stays clear at the brightest animation frame").toBe(true);
  await expect(icon.locator("img")).toBeVisible();

  // Even the breathing layers of a subpixel execution keep its recorded length.
  durationMs = 0.5 / ppm * 60_000;
  await page.reload();
  await expect(bar).toBeVisible();
  const shortWidth = await bar.evaluate(node => (node as SVGRectElement).getBBox().width);
  const edgeWidth = await edge.evaluate(node => (node as SVGRectElement).getBBox().width);
  expect(shortWidth).toBeGreaterThan(0);
  expect(shortWidth).toBeLessThan(1);
  expect(edgeWidth).toBeLessThanOrEqual(shortWidth + 0.01);
  const external = (await icon.boundingBox())!;
  await page.mouse.move(external.x + external.width / 2, external.y + external.height / 2);
  await expect(page.locator('[data-slot=timeline-status]')).toContainText("claude / anthropic / claude-opus-5-5 /");
});
