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
  const ppm = Number(await page.locator("[data-slot=timeline-canvas]").getAttribute("data-ppm"));
  expect(ppm).toBeGreaterThan(0);

  await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
    const response = await route.fetch();
    const timeline: MacroTaskTimeline = await response.json();
    const live = timeline.spans.find(span => span.spanId === spanId)!;
    live.startAt = new Date(Date.parse(timeline.observedAt) - 10 / ppm * 60_000).toISOString();
    live.reminders = null;
    live.expectedSeconds = null;
    live.inquiries = [];
    timeline.events = timeline.events.filter(event => event.runId !== live.runId);
    await route.fulfill({ response, json: timeline });
  });
  await page.reload();

  const segment = page.locator(`[data-key="span:${spanId}"]`);
  const bar = segment.locator('[data-slot=execution-bar][data-outcome=running]');
  const edge = segment.locator('[data-slot=execution-breath]');
  const icon = segment.locator('[data-slot=execution-buddy]');
  await expect(edge).toBeVisible();
  await expect(icon).toBeVisible();
  await icon.locator("img").evaluate(node => (node as HTMLImageElement).decode());
  await icon.scrollIntoViewIfNeeded();
  const [barBox, iconBox] = await Promise.all([bar.boundingBox(), icon.boundingBox()]);
  expect(barBox!.width).toBeGreaterThan(7);
  expect(barBox!.width).toBeLessThan(iconBox!.width);
  expect(barBox!.x + barBox!.width).toBeGreaterThan(iconBox!.x + 3);

  await edge.evaluate(node => { const motion = node.getAnimations()[0]!; motion.pause(); motion.currentTime = 0; });
  const first = await icon.boundingBox();
  const edgeIsBehind = await icon.evaluate(node => {
    const edge = node.closest("[data-key]")!.querySelector("[data-slot=execution-breath]")!;
    return Boolean(node.compareDocumentPosition(edge) & Node.DOCUMENT_POSITION_PRECEDING)
      && Boolean(node.compareDocumentPosition(edge.parentElement!) & Node.DOCUMENT_POSITION_PRECEDING);
  });
  expect(edgeIsBehind, "both the breathing edge and its backing paint before the icon").toBe(true);
  await edge.evaluate(node => { node.getAnimations()[0]!.currentTime = 1000; });
  expect(await icon.boundingBox(), "the icon never moves with the edge").toEqual(first);
  await expect(icon.locator("img")).toBeVisible();
});
