import { expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const captures = fileURLToPath(new URL("../../../tmp/readability-r17", import.meta.url));

for (const theme of ["dark", "light"]) for (const width of [1440, 390]) test.describe(`multiple buddies ${theme} ${width}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test("draws distinct buddy colours and shares the first sample with the legend", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    let executionCount = 0;
    await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
      const response = await route.fetch();
      const timeline = await response.json() as MacroTaskTimeline;
      let index = 0;
      for (const span of timeline.spans) if (span.kind === "execution") {
        span.buddy = { ...span.buddy!, model: `sample-${++index}` };
        timeline.rows.find(row => row.runId === span.runId)!.buddy = span.buddy;
      }
      executionCount = index;
      await route.fulfill({ response, json: timeline });
    });
    await open(page, `#tasks/${macro}`);
    const bars = page.locator('[data-slot=execution-bar]');
    await expect(bars.first()).toBeVisible();
    expect(executionCount).toBeGreaterThan(4);
    await expect(bars).toHaveCount(executionCount);
    expect(new Set(await bars.evaluateAll(nodes => nodes.map(node => (node as SVGElement).style.fill))).size).toBe(executionCount);
    for (const bar of await bars.all()) await expect(bar).toBeInViewport();
    const first = await bars.first().evaluate(node => getComputedStyle(node).fill);
    await page.locator('[data-slot=legend-trigger]').click();
    const sample = page.locator('[data-slot=timeline-legend] [data-kind=execution]').locator('span[style]').first();
    await expect(sample).toHaveCSS("background-color", first);
    await page.keyboard.press("Escape");
    await page.mouse.move(0, 0);
    await mkdir(captures, { recursive: true });
    await page.screenshot({ path: `${captures}/many-buddies-${theme}-${width}.png` });
  });
});
