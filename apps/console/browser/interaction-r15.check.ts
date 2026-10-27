import { expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { open, test } from "./preview";

test.use({ locale: "zh-CN", reducedMotion: "reduce" });

const macro = "macro-11111111-1111-4111-8111-111111111111";
const captures = fileURLToPath(new URL("../../../tmp/readability-r15", import.meta.url));
const back = (page: Page) => page.locator('[data-slot=detail-header]').getByRole("button", { name: "收起详情" });

for (const theme of ["dark", "light"]) for (const width of [1440, 390]) test.describe(`round 15 interaction ${theme} ${width}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
  });

  test("opens fragments and cluster entries once, preserves the selected row, and keeps the detail footer empty", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    const timeline = await (await page.request.get(`/api/macro-tasks/${macro}/timeline`)).json() as MacroTaskTimeline;
    const event = timeline.events.find(event => event.runId === "preview-run-a1" && event.kind === "continue")!;
    for (const [runId, key, step] of [
      ["preview-run-a1", "span:execution:att-a1-2", 7],
      ["preview-run-a1", "span:queue:att-a1-1", 2],
      ["preview-run-a1", "span:host:req-a1-1", 4],
      ["preview-run-a3", "inquiry:inq-a3-1", 3],
      ["preview-run-a1", `event:${event.seq}`, 5],
    ]) {
      await page.locator('[data-slot=zoom-ruler]:visible').press("+");
      const item = page.locator(`[data-slot=timeline-tracks] [data-key="${key}"]`);
      // A cluster owns nearby events at this scale; its list is the click target.
      if (await item.count()) await item.click();
      else {
        const cluster = page.locator(`[data-run-id="${runId}"] [data-slot=event-cluster]`).first();
        await cluster.click();
        const label = event.kind === "continue" ? "续接" : "";
        await page.getByRole("dialog").getByRole("button", { name: new RegExp(label) }).first().click();
      }
      await expect(page).toHaveURL(new RegExp(`#tasks/${macro}/${runId}/${step}$`));
      await expect(page.locator('[data-slot=timeline-status] [role=status]')).toBeEmpty();
      await expect(page.locator('[data-slot=timeline-status] [data-slot=legend-trigger]')).toBeVisible();
      await back(page).click();
      await expect(page.locator(`[data-slot=timeline-row-name][data-run-id="${runId}"]`)).toHaveClass(/bg-selected-row/);
    }
    await page.locator('[data-slot=zoom-ruler]:visible').press("0");
    const span = page.locator('[data-key="span:execution:att-a1-2"]');
    const box = (await span.locator('[data-slot=execution-bar]').boundingBox())!;
    let reads = 0;
    page.on("request", request => { if (new URL(request.url()).pathname === "/api/tasks/preview-run-a1") reads++; });
    await page.mouse.dblclick(box.x + box.width / 2, box.y + box.height / 2);
    await expect(page).toHaveURL(new RegExp(`/preview-run-a1/7$`));
    expect(reads).toBe(1);
    await mkdir(captures, { recursive: true });
    await page.screenshot({ path: `${captures}/detail-${theme}-${width}.png` });
  });

  test("uses transient hover and keyboard frames, selects only on blank space, and anchors keyboard zoom on the focused item", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    const item = page.locator('[data-key="span:execution:att-a1-2"]');
    const row = page.locator('[data-slot=timeline-row][data-run-id="preview-run-a1"]');
    const status = page.locator('[data-slot=timeline-status] [role=status]');
    await item.hover();
    await expect(item.locator('[data-slot=selected-item]')).toHaveCount(1);
    await expect(row.locator('[data-slot=selected-row]')).toHaveCount(1);
    await expect(status).toContainText("回合 2");
    await page.mouse.move(2, 2);
    await expect(item.locator('[data-slot=selected-item]')).toHaveCount(0);
    await expect(row.locator('[data-slot=selected-row]')).toHaveCount(0);
    await item.focus();
    const x = (await item.locator('[data-slot=execution-bar]').boundingBox())!.x;
    await page.keyboard.press("+");
    expect(Math.abs((await item.locator('[data-slot=execution-bar]').boundingBox())!.x - x)).toBeLessThanOrEqual(1);
    await expect(item.locator('[data-slot=selected-item]')).toHaveCount(1);
    await item.evaluate(node => (node as SVGElement).blur());
    const trackBox = (await row.locator('[data-slot=timeline-track]').boundingBox())!;
    const viewBox = (await page.locator('[data-slot=timeline-tracks]').boundingBox())!;
    await page.mouse.click(viewBox.x + viewBox.width - 20, trackBox.y + 4);
    await expect(page.locator('[data-slot=task-work]')).toHaveAttribute("data-mode", "timeline");
    await expect(row.locator('[data-slot=selected-row]')).toHaveCount(1);
    const view = page.locator('[data-slot=timeline-tracks]');
    await view.evaluate(node => { node.scrollTop = node.scrollHeight; });
    const labels = view.locator('[data-slot=timeline-track]').last().locator('[data-slot=track-label]');
    const footer = (await page.locator('[data-slot=timeline-status]').boundingBox())!;
    for (const label of await labels.all()) {
      const box = (await label.boundingBox())!;
      expect(box.y + box.height).toBeLessThanOrEqual(footer.y);
    }
    await mkdir(captures, { recursive: true });
    await page.screenshot({ path: `${captures}/timeline-${theme}-${width}.png` });
  });
});

test("retains the name heading inset and lane totals beside a detail, and aligns copied IDs with other facts", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const beta = "macro-22222222-2222-4222-8222-222222222222";
  await open(page, `#tasks/${beta}`);
  const header = page.getByText("微任务", { exact: true });
  const x = (await header.boundingBox())!.x;
  await page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-b1"]').click();
  expect(Math.abs((await header.boundingBox())!.x - x)).toBeLessThanOrEqual(1);
  await page.locator('[data-slot=task-names] [data-run-id="preview-run-b1"][data-slot=timeline-row-name]').hover();
  await expect(page.getByRole("tooltip")).toContainText("3 个微任务");
  await page.evaluate(hash => { window.location.hash = hash; }, `#tasks/${macro}/preview-run-a1/1`);
  const id = page.locator('[data-slot=detail-pane] button[aria-label*="preview-run-a1"]').first();
  await expect(id).toBeVisible();
  const geometry = await id.evaluate(button => {
    const cell = button.closest("dd")!.getBoundingClientRect();
    const text = button.querySelector("span")!.getBoundingClientRect();
    const box = button.getBoundingClientRect();
    return { text: text.left, cell: cell.left, hit: box.left };
  });
  expect(Math.abs(geometry.text - geometry.cell)).toBeLessThanOrEqual(1);
  expect(geometry.text - geometry.hit).toBeGreaterThanOrEqual(8);
});
