import { expect, type Locator, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, test } from "./preview";
import type { MacroTaskTimeline } from "../src/macro-task-types";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";
const captures = fileURLToPath(new URL("../../../tmp/readability-r6", import.meta.url));
async function shot(page: Page, name: string) {
  await mkdir(captures, { recursive: true });
  await page.screenshot({ path: `${captures}/${name}.png` });
}
async function middle(node: Locator) {
  const box = (await node.boundingBox())!;
  return box.x + box.width / 2;
}

test("shares one vertical axis per tree level in the timeline and detail list", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
  await page.route(`**/api/macro-tasks/${B}/timeline*`, async route => {
    const response = await route.fetch();
    const timeline = await response.json() as MacroTaskTimeline;
    const parent = timeline.rows.find(row => row.runId === "preview-run-b4")!;
    const ownHelper = timeline.rows.find(row => row.runId === "preview-run-b1-h1")!;
    timeline.rows.splice(timeline.rows.indexOf(parent) + 1, 0, { ...ownHelper, runId: "axis-helper", parentRunId: parent.runId,
      rootRunId: parent.runId, rootLeadRunId: parent.rootLeadRunId, depth: 1, title: "Axis helper", titleSource: "title" });
    timeline.totals.rows++; timeline.totals.allRows++;
    timeline.macroTask.counts.helpers++; timeline.macroTask.matchingRuns++;
    await route.fulfill({ response, json: timeline });
  });
  await open(page, `#tasks/${B}`);
  const axes = async (slot: "timeline-lane" | "detail-lane") => {
    const lane = page.locator(`[data-slot=${slot}]`).filter({ has: page.locator('[data-key="row:preview-run-b3"]') });
    const axis = await middle(lane.locator('[data-slot=lane-toggle] [data-slot=disclosure]'));
    const member = lane.locator('[data-key="row:preview-run-b4"]');
    const helper = lane.locator('[data-key="row:axis-helper"]');
    for (const node of [member.locator('[data-slot=lane-guide]'), member.locator('[data-slot=row-state-edge]'), helper.locator('[data-slot=lane-guide][data-level="1"]')]) {
      expect(await middle(node)).toBeCloseTo(axis, 1);
    }
    for (const node of [helper.locator('[data-slot=lane-guide][data-level="2"]'), helper.locator('[data-slot=row-state-edge]')]) {
      expect(await middle(node)).toBeCloseTo(axis + 18, 1);
    }
  };
  await axes("timeline-lane");
  await page.locator('[data-key="row:preview-run-b3"]').click();
  await expect(page.locator('[data-slot=task-names]')).toBeVisible();
  await axes("detail-lane");
  await shot(page, "axes-detail-dark-1440");
});

async function barLabels(page: Page) {
  return page.locator('[data-slot=timeline-track]:visible').evaluateAll(tracks => {
    const outside: string[] = [];
    let count = 0;
    for (const track of tracks) {
      const bars = [...track.querySelectorAll('[data-slot=execution-bar]')].map(bar => bar.getBoundingClientRect());
      for (const label of track.querySelectorAll('[data-slot=track-label].fill-execution-ink, [data-slot=track-label].fill-execution-ink-light')) {
        const text = label.getBoundingClientRect();
        count++;
        const bar = bars.find(bar => text.left >= bar.left - 0.75 && text.left < bar.right && text.top < bar.bottom && text.bottom > bar.top);
        if (!bar || text.right > bar.right + 0.75) outside.push(label.textContent ?? "");
      }
    }
    return { count, outside };
  });
}

for (const view of ["timeline", "detail"]) for (const width of [1440, 390]) for (const theme of ["dark", "light"]) test.describe(`hidden measurement ${view} ${width} ${theme}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test("remeasures translated labels after a hidden poll in both language directions", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, `#tasks/${A}${view === "detail" ? "/preview-run-a1/7" : ""}`);
    if (width === 390) {
      // At the fitted narrow scale some translated executions are too short for text.
      const ruler = page.locator('[data-slot=zoom-ruler]:visible');
      await ruler.press("+");
      await ruler.press("+");
    }
    await expect.poll(async () => (await barLabels(page)).count).toBeGreaterThan(0);
    const nav = page.getByRole("banner").first().getByRole("navigation");
    for (const language of ["English", "中文"]) {
      await nav.getByRole("link").last().click();
      await expect(page.locator('[data-slot=task-work]')).toBeHidden();
      await page.getByRole("radio", { name: language, exact: true }).click();
      const hiddenWidths = await page.locator('[data-slot=timeline-track] > g[visibility=hidden] text').evaluateAll(nodes => nodes
        .filter(node => node.textContent).map(node => (node as SVGTextElement).getComputedTextLength()));
      expect(hiddenWidths.length).toBeGreaterThan(0);
      expect(hiddenWidths.every(width => width === 0)).toBe(true);
      // Start observing after the language change, so this is a later poll.
      const request = await page.waitForRequest(request => new URL(request.url()).pathname.endsWith("/api/console"));
      expect((await request.response())?.status()).toBeLessThan(400);
      await nav.getByRole("link").first().click();
      await expect(page.locator('[data-slot=task-work]')).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await expect.poll(async () => (await barLabels(page)).count).toBeGreaterThan(0);
      await expect.poll(async () => (await barLabels(page)).outside).toEqual([]);
    }
    await shot(page, `measure-${view}-${theme}-${width}`);
  });
});
