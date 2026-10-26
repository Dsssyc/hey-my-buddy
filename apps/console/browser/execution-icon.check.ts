import { expect } from "@playwright/test";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const spanId = "execution:att-a3-1";

test.use({ viewport: { width: 1440, height: 1000 }, reducedMotion: "no-preference" });

test("a running bar narrower than its buddy icon never paints over the icon", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-09-27T10:00:00.000Z"));
  await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
  await open(page, `#tasks/${macro}`);

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
  // A 12px fill plus its 1px stroke stays narrower than the 14px icon while
  // covering the icon's centre, where the breathing layers used to obscure it.
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
  expect(barBox!.x + barBox!.width).toBeGreaterThan(iconBox!.x + iconBox!.width / 2);

  await edge.evaluate(node => { const motion = node.getAnimations()[0]!; motion.pause(); motion.currentTime = 0; });
  const first = await icon.boundingBox();
  const iconIsOnTop = () => segment.evaluate(group => {
    const edge = group.querySelector<SVGRectElement>("[data-slot=execution-breath]")!;
    const backing = edge.previousElementSibling as SVGRectElement;
    const paper = group.querySelector<SVGRectElement>("[data-slot=execution-buddy-paper]")!;
    const icon = group.querySelector<SVGForeignObjectElement>("[data-slot=execution-buddy]")!;
    const center = icon.getBoundingClientRect();
    const x = center.left + center.width / 2;
    const y = center.top + center.height / 2;
    const edgeBox = edge.getBoundingClientRect();
    if (x <= edgeBox.left || x >= edgeBox.right || y <= edgeBox.top || y >= edgeBox.bottom) return false;
    // These SVG layers ignore pointer events in product use. Enable hit testing
    // only for this probe, so elementFromPoint reports their visual stack.
    const layers = [edge, backing, paper, icon];
    const previous = layers.map(layer => layer.style.pointerEvents);
    try {
      for (const layer of layers) layer.style.pointerEvents = "auto";
      return icon.contains(document.elementFromPoint(x, y));
    } finally {
      layers.forEach((layer, index) => { layer.style.pointerEvents = previous[index]!; });
    }
  });
  expect(await iconIsOnTop(), "the icon paints above both breathing edge layers at its centre").toBe(true);
  await edge.evaluate(node => { node.getAnimations()[0]!.currentTime = 1000; });
  expect(await icon.boundingBox(), "the icon never moves with the edge").toEqual(first);
  expect(await iconIsOnTop(), "the icon remains on top at the brightest animation frame").toBe(true);
  await expect(icon.locator("img")).toBeVisible();
});
