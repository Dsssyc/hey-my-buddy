import { expect, type Locator, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { open, test } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const captures = fileURLToPath(new URL("../../../tmp/readability-r10", import.meta.url));
const ruler = (page: Page) => page.locator('[data-slot=zoom-ruler]:visible');
async function shot(page: Page, name: string) { await mkdir(captures, { recursive: true }); await page.screenshot({ path: `${captures}/${name}.png` }); }
async function fitGeometry(page: Page) {
  const axis = page.locator('[data-slot=timeline-axis]:visible');
  const fit = (await page.locator('[data-slot=zoom-fit]').boundingBox())!;
  const text = (await axis.locator('[data-slot=axis-label].fill-ink2').first().boundingBox())!;
  const line = (await axis.locator('line[y1="30"][y2="30"]').boundingBox())!;
  expect(fit.width).toBe(20); expect(fit.height).toBe(20);
  expect(fit.y + fit.height).toBeLessThan(line.y);
  expect(Math.abs(fit.y + fit.height / 2 - text.y - text.height / 2)).toBeLessThanOrEqual(1);
}

for (const theme of ["dark", "light"]) for (const width of [1440, 390]) test.describe(`legend and fit ${theme} ${width}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test("keeps one readout row, opens the complete legend by keyboard and aligns both fit buttons to the clock text", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, `#tasks/${A}`);
    const status = page.locator('[data-slot=timeline-status]');
    const retained = (await status.elementHandle())!;
    const trigger = status.locator('[data-slot=legend-trigger]');
    await expect(trigger).toHaveText("图例");
    await expect(page.locator('[data-slot=timeline-legend-footer]')).toHaveCount(0);
    await expect(page.locator('[data-slot=timeline-legend]')).toHaveCount(0);
    const statusBox = (await status.boundingBox())!, triggerBox = (await trigger.boundingBox())!;
    expect(triggerBox.x).toBeGreaterThan(statusBox.x + statusBox.width / 2);
    await ruler(page).press("+");
    await fitGeometry(page);
    await trigger.focus(); await page.keyboard.press("Enter");
    const card = page.getByRole("dialog", { name: "图例", exact: true });
    await expect(card).toBeVisible();
    await expect(card.locator('[data-slot=legend-item]')).toHaveCount(10);
    await expect(card.locator('[data-kind=waiting]')).toContainText("等待 Host 或 Lead 决定或验收的时段。");
    await expect(card.locator('[data-kind=cluster]')).toContainText("挨近的记号合成一个，数字是个数，点开看每一项。");
    await expect(card.locator('[data-kind=inquiry]')).toContainText("Host 向运行中的回合提出的问题。");
    await expect(card.locator('[data-slot=tooltip-trigger]')).toHaveCount(0);
    const box = (await card.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(width);
    expect(box.y).toBeGreaterThanOrEqual(0); expect(box.y + box.height).toBeLessThanOrEqual(1000);
    await shot(page, `legend-${theme}-${width}`);
    await page.keyboard.press("Escape");
    await expect(card).toHaveCount(0); await expect(trigger).toBeFocused();
    await expect(page.locator('[data-slot=task-work]')).toHaveAttribute("data-mode", "timeline");
    await page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a1"]').click();
    await expect(page.locator('[data-slot=task-work]')).toHaveAttribute("data-mode", "detail");
    expect(await retained.evaluate(node => node.isConnected)).toBe(true);
    expect((await status.boundingBox())!.height).toBe(statusBox.height);
    await ruler(page).press("+");
    await fitGeometry(page);
    await trigger.focus(); await page.keyboard.press("Enter");
    await expect(card).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(trigger).toBeFocused();
    await expect(page.locator('[data-slot=task-work]')).toHaveAttribute("data-mode", "detail");
    await shot(page, `detail-${theme}-${width}`);
  });
});

for (const width of [1440, 390]) test.describe(`stationary pointer ${width}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test("keeps the line under the untouched mouse and derives its clock from the current scale after zoom and pan", async ({ page }) => {
    await open(page, `#tasks/${A}`);
    const response = await page.request.get(`/api/macro-tasks/${A}/timeline`);
    const timeline = await response.json() as MacroTaskTimeline;
    const span = timeline.spans.find(span => span.runId === "preview-run-a1" && span.kind === "execution" && span.endAt !== null)!;
    const bar = page.locator(`[data-key="span:${span.spanId}"] [data-slot=execution-bar]`);
    const rect = (await bar.boundingBox())!;
    const axis = (await page.locator('[data-slot=timeline-axis]').boundingBox())!;
    const mouse = { x: rect.x + rect.width / 2, y: axis.y + 12 };
    await page.mouse.move(mouse.x, mouse.y);
    const pointer = page.locator('[data-slot=timeline-pointer]');
    const clock = page.locator('[data-slot=pointer-clock] text');
    const view = page.locator('[data-slot=timeline-tracks]');
    const check = async () => {
      await expect.poll(async () => Math.abs((await pointer.boundingBox())!.x - mouse.x)).toBeLessThanOrEqual(1);
      const current = (await bar.boundingBox())!;
      const fraction = (mouse.x - current.x) / current.width;
      expect(fraction).toBeGreaterThan(0); expect(fraction).toBeLessThan(1);
      const at = Date.parse(span.startAt!) + fraction * (Date.parse(span.endAt!) - Date.parse(span.startAt!));
      const expected = await page.evaluate(at => new Intl.DateTimeFormat("zh-CN", { hourCycle: "h23", hour: "2-digit", minute: "2-digit" }).format(new Date(at)), at);
      await expect(clock).toHaveText(expected);
    };
    await check();
    const initial = await clock.textContent();
    for (let index = 0; index < 4; index++) {
      const before = Number(await page.locator('[data-slot=timeline-canvas]').getAttribute("data-ppm"));
      await page.mouse.wheel(0, -120);
      await expect.poll(async () => Number(await page.locator('[data-slot=timeline-canvas]').getAttribute("data-ppm"))).toBeGreaterThan(before);
      await check();
      await expect(clock).toHaveText(initial!);
    }
    for (const delta of [12, 14]) {
      const before = await view.evaluate(node => node.scrollLeft);
      await page.mouse.wheel(delta, 0);
      await expect.poll(() => view.evaluate(node => node.scrollLeft)).toBeGreaterThan(before);
      await check();
    }
    expect(await clock.textContent()).not.toBe(initial);
  });
});
