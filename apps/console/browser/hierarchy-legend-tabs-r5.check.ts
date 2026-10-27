import { expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, openLegend, test } from "./preview";
import type { MacroTaskTimeline } from "../src/macro-task-types";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";
const captures = fileURLToPath(new URL("../../../tmp/readability-r5", import.meta.url));

async function shot(page: Page, name: string) {
  await mkdir(captures, { recursive: true });
  await page.screenshot({ path: `${captures}/${name}.png` });
}

test("keeps the Lead's own helper and the owned helper's depth in both views", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
  await page.route(`**/api/macro-tasks/${B}/timeline*`, async route => {
    const response = await route.fetch();
    const timeline = await response.json() as MacroTaskTimeline;
    const own = timeline.rows.find(row => row.runId === "preview-run-b1-h1")!;
    const parent = timeline.rows.find(row => row.runId === "preview-run-b4")!;
    timeline.rows.splice(timeline.rows.indexOf(parent) + 1, 0, { ...own, runId: "owned-helper", parentRunId: parent.runId, rootRunId: parent.runId,
      rootLeadRunId: parent.rootLeadRunId, depth: 1, title: "Owned helper", titleSource: "title" });
    timeline.spans.push(...timeline.spans.filter(span => span.runId === own.runId).map(span => ({ ...span, runId: "owned-helper", spanId: `owned-helper:${span.spanId}` })));
    timeline.totals.rows++; timeline.totals.allRows++;
    timeline.macroTask.counts.helpers++; timeline.macroTask.matchingRuns++;
    await route.fulfill({ response, json: timeline });
  });
  await open(page, `#tasks/${B}`);
  const assertHierarchy = async (view: "timeline" | "detail") => {
    const groupSlot = view === "timeline" ? "timeline-lane" : "detail-lane";
    const ownLane = page.locator(`[data-slot=${groupSlot}]`).filter({ has: page.locator('[data-key="row:preview-run-b1"]') });
    await expect(ownLane.locator('[data-key="row:preview-run-b1-h1"]')).toBeVisible();
    const root = view === "timeline" ? page.locator('[data-slot=timeline-tracks]') : page.locator('[data-slot=task-names]');
    for (const [id, level, helper] of [["preview-run-b1-h1", 1, true], ["preview-run-b4", 1, false], ["owned-helper", 2, true]] as const) {
      const row = root.locator(`[data-key="row:${id}"]`);
      await expect(row).toHaveCSS("padding-left", `${13 + level * 18}px`);
      await expect(row.locator('[data-slot=lane-guide]')).toHaveCount(level);
      await expect(row.getByText("└", { exact: true })).toHaveCount(helper ? 1 : 0);
      const edge = (await row.locator('[data-slot=row-state-edge]').boundingBox())!;
      const guide = (await row.locator('[data-slot=lane-guide]').last().boundingBox())!;
      expect(edge.x + edge.width / 2).toBeCloseTo(guide.x + guide.width / 2, 1);
      expect(edge.x + edge.width / 2 - (await row.boundingBox())!.x).toBe(14 + (level - 1) * 18);
    }
  };
  await assertHierarchy("timeline");
  await page.locator('[data-key="row:preview-run-b3"]').click();
  await expect(page.locator('[data-slot=task-names]')).toBeVisible();
  await assertHierarchy("detail");
  await shot(page, "lead-hierarchy-dark-1440");
});

for (const locale of ["zh-CN", "en-US"]) test.describe(`execution legend ${locale}`, () => {
  test.use({ locale, viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  test("uses three adjacent buddy colours and explains Host inquiries", async ({ page }) => {
    await open(page, `#tasks/${A}`);
    const legend = await openLegend(page);
    const samples = page.locator('[data-slot=legend-execution-color]');
    await expect(samples).toHaveCount(3);
    const colors = await samples.evaluateAll(nodes => nodes.map(node => getComputedStyle(node).backgroundColor));
    expect(new Set(colors).size).toBe(3);
    for (const color of colors) expect(new Set(color.match(/\d+/g)).size).toBeGreaterThan(1);
    const boxes = await samples.all().then(items => Promise.all(items.map(item => item.boundingBox())));
    for (let index = 1; index < boxes.length; index++) expect(boxes[index]!.x).toBeCloseTo(boxes[index - 1]!.x + boxes[index - 1]!.width, 1);
    const barColors = await page.locator('[data-slot=execution-bar][data-outcome=finished]').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).fill));
    // At least the earliest completed execution matches the first prefix colour.
    expect(barColors).toContain(colors[0]);
    await expect(legend.locator('[data-kind=execution]')).toContainText(locale === "zh-CN" ? "buddy 执行工作的时段，颜色区分 buddy。" : "Time a buddy spent working; colours distinguish buddies.");
    await expect(legend.locator('[data-kind=inquiry]')).toContainText(locale === "zh-CN" ? "Host 向运行中的回合提出的问题。" : "A question from the Host to a running turn.");
  });
});

for (const width of [1440, 390]) for (const theme of ["dark", "light"]) test.describe(`text-width tabs ${width} ${theme}`, () => {
  test.use({ viewport: { width, height: 1000 }, locale: "en-US", reducedMotion: "reduce" });
  test("keeps both labels at the left and sizes them to their text", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, `#tasks/${A}/preview-run-a1/7`);
    const list = page.locator('[data-slot=detail-tabs] [role=tablist]');
    await expect(list).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const geometry = await list.evaluate(node => {
      const outer = node.getBoundingClientRect();
      return { outer: { x: outer.x, width: outer.width }, tabs: [...node.querySelectorAll('[role=tab]')].map(tab => {
        const box = tab.getBoundingClientRect(), text = document.createRange(); text.selectNodeContents(tab);
        return { x: box.x, right: box.right, width: box.width, text: text.getBoundingClientRect().width };
      }) };
    });
    expect(geometry.tabs).toHaveLength(2);
    expect(geometry.tabs[0]!.x - geometry.outer.x).toBeLessThanOrEqual(24);
    expect(geometry.tabs[1]!.right - geometry.outer.x).toBeLessThan(geometry.outer.width / 2);
    for (const tab of geometry.tabs) expect(tab.width - tab.text).toBeLessThan(2);
    await list.getByRole("tab", { name: "Session", exact: true }).click();
    await expect(page.locator('[data-slot=session-view]').getByText("Ended", { exact: true })).toBeVisible();
    await shot(page, `tabs-session-${theme}-${width}`);
  });
});
