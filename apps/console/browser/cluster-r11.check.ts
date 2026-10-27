import { expect, type Page } from "@playwright/test";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { open, openLegend, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";

async function manyCoincidentEvents(page: Page) {
  await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
    const response = await route.fetch();
    const timeline = await response.json() as MacroTaskTimeline;
    const source = timeline.events.find(event => event.runId === "preview-run-a2" && event.kind === "decide")!;
    timeline.events = [
      ...timeline.events.filter(event => event.runId !== source.runId),
      ...Array.from({ length: 123 }, (_, index) => ({ ...source, seq: 10_000 + index, kind: "reject", at: source.at })),
    ];
    timeline.totals.events = timeline.events.length;
    await route.fulfill({ response, json: timeline });
  });
}

for (const { theme, width } of [{ theme: "dark", width: 390 }, { theme: "light", width: 1440 }]) {
  test.describe(`cluster geometry ${theme} ${width}px`, () => {
    test.use({ viewport: { width, height: 900 }, reducedMotion: "reduce" });

    test("keeps the circle marks at 16 by 16 with a 1 px stroke", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/${macro}`);
      const legend = await openLegend(page);
      for (const kind of ["accepted", "failed", "cluster"] as const) {
        const circle = legend.locator(`[data-kind="${kind}"] circle`);
        await expect(circle).toHaveCount(1);
        const dimensions = await circle.evaluate(node => ({
          width: node.getBoundingClientRect().width,
          height: node.getBoundingClientRect().height,
          stroke: getComputedStyle(node).strokeWidth,
        }));
        expect(dimensions, kind).toEqual({ width: 16, height: 16, stroke: "1px" });
      }
      await page.keyboard.press("Escape");
      const timelineCluster = page.locator('[data-slot="event-cluster"] [data-slot="cluster-mark"] circle').first();
      await expect(timelineCluster).toBeVisible();
      expect(await timelineCluster.evaluate(node => ({
        width: node.getBoundingClientRect().width,
        height: node.getBoundingClientRect().height,
        stroke: getComputedStyle(node).strokeWidth,
      }))).toEqual({ width: 16, height: 16, stroke: "1px" });
    });

    test("fits 123 inside a measured capsule and keeps its selection and focus frames outside", async ({ page }) => {
      await manyCoincidentEvents(page);
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/${macro}`);
      await page.evaluate(() => document.fonts.ready);
      const cluster = page.locator('[data-slot="timeline-row"][data-run-id="preview-run-a2"] [data-slot="event-cluster"]');
      await expect(cluster).toHaveCount(1);
      const mark = cluster.locator('[data-slot="cluster-mark"]');
      await expect(mark.locator("text")).toHaveText("123");
      const sizes = await mark.evaluate(node => {
        const capsule = node.querySelector("rect:not([data-slot])")!;
        const text = node.querySelector("text")!;
        const frame = node.querySelector('[data-slot="fragment-focus"]')!;
        const pill = capsule.getBoundingClientRect();
        const glyphs = text.getBoundingClientRect();
        return {
          width: pill.width, height: pill.height, stroke: getComputedStyle(capsule).strokeWidth,
          measuredText: text.getComputedTextLength(),
          leftPadding: glyphs.left - pill.left, rightPadding: pill.right - glyphs.right,
          focusWidth: frame.getBoundingClientRect().width,
        };
      });
      expect(sizes.height).toBe(16);
      expect(sizes.stroke).toBe("1px");
      expect(sizes.width).toBeGreaterThanOrEqual(sizes.measuredText + 8 - 0.5);
      expect(sizes.leftPadding).toBeGreaterThanOrEqual(3.5);
      expect(sizes.rightPadding).toBeGreaterThanOrEqual(3.5);
      expect(sizes.focusWidth).toBeGreaterThan(sizes.width);
      await cluster.click();
      await expect(cluster).toHaveAttribute("aria-pressed", "true");
      const selectedWidth = await mark.locator('[data-slot="selected-item"]').evaluate(node => node.getBoundingClientRect().width);
      expect(selectedWidth).toBe(sizes.focusWidth);
    });
  });
}
