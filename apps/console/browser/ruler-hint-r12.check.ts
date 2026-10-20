import { expect, type Locator } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const hint = "滚轮缩放 · Shift+滚轮平移 · 双击适应宽度 · 键盘 + − 0";
const captures = fileURLToPath(new URL("../../../tmp/readability-r12", import.meta.url));
async function box(node: Locator) { return (await node.boundingBox())!; }

for (const theme of ["dark", "light"]) for (const width of [1440, 390]) for (const detail of [false, true]) test.describe(`ruler hint ${theme} ${width} ${detail ? "detail" : "timeline"}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test("strengthens the ruler, follows the pointer without restarting its delay and stays inside the viewport", async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem("hey-my-buddy.console.theme", theme), theme);
    await page.clock.install({ time: new Date("2026-10-19T12:00:00Z") });
    await open(page, `#tasks/${macro}${detail ? "/preview-run-a1/3" : ""}`);
    const ruler = page.locator('[data-slot=zoom-ruler]:visible');
    await expect(ruler).toBeVisible();
    await ruler.press("+"); await ruler.press("+");
    const viewport = page.locator(detail ? '[data-slot=detail-strip-scroll]' : '[data-slot=timeline-tracks]');
    await viewport.evaluate(node => node.scrollLeft = 225);
    await expect.poll(() => viewport.evaluate(node => node.scrollLeft)).toBe(225);
    await page.mouse.move(0, 0);
    await page.evaluate(() => (document.activeElement as HTMLElement).blur());
    await page.clock.pauseAt(new Date("2026-10-19T12:01:00Z"));
    const axis = ruler.locator('[data-slot=timeline-axis]');
    const colors = await axis.evaluate(node => {
      const sample = document.createElement("span"); document.body.append(sample);
      const color = (name: string) => { sample.style.color = `var(${name})`; return getComputedStyle(sample).color; };
      const result = { line: getComputedStyle(node.querySelector('line')!).stroke, text: getComputedStyle(node.querySelector('[data-slot=axis-label].fill-ink2')!).fill,
        strongerLine: color("--ink3"), strongerText: color("--ink2") };
      sample.remove(); return result;
    });
    expect(colors.line).toBe(colors.strongerLine); expect(colors.text).toBe(colors.strongerText);
    const background = await ruler.evaluate(node => getComputedStyle(node).backgroundColor);
    const frame = await box(viewport), rulerBox = await box(ruler);
    const nameHeader = ruler.locator(':scope > div.sticky');
    const left = frame.x + (await nameHeader.count() ? (await box(nameHeader)).width : 0) + 12;
    const right = frame.x + frame.width - 12;
    const middle = (left + right) / 2;
    const y = rulerBox.y + 12;
    const tooltip = page.locator('[data-slot=tooltip-content]').filter({ hasText: hint });
    const closeTo = async (x: number) => {
      await expect(tooltip).toBeVisible();
      await expect.poll(async () => { const tip = await box(tooltip); return Math.abs(tip.x + tip.width / 2 - x) - tip.width / 2; }).toBeLessThanOrEqual(1);
      const tip = await box(tooltip);
      expect(tip.x).toBeGreaterThanOrEqual(frame.x);
      expect(tip.x + tip.width).toBeLessThanOrEqual(frame.x + frame.width);
      expect(tip.y).toBeGreaterThanOrEqual(0); expect(tip.y + tip.height).toBeLessThanOrEqual(1000);
      return tip;
    };
    for (const x of [left, middle, right]) {
      await page.mouse.move(x, y + 40); await expect(tooltip).toHaveCount(0);
      await page.mouse.move(x, y);
      await page.clock.runFor(500); await expect(tooltip).toHaveCount(0);
      await page.clock.runFor(100); await closeTo(x);
      await expect(ruler).toHaveCSS("background-color", background);
    }
    const retained = (await tooltip.elementHandle())!;
    const previous = await box(tooltip);
    await page.mouse.move(left, y);
    const moved = await closeTo(left); // No time has advanced since it opened.
    expect(await retained.evaluate(node => node.isConnected)).toBe(true);
    if (previous.width < frame.width - 32) expect(moved.x).toBeLessThan(previous.x - 10);
    await mkdir(captures, { recursive: true });
    await page.screenshot({ path: `${captures}/${detail ? "detail" : "timeline"}-${theme}-${width}.png` });
    await page.mouse.move(left, y + 40); await expect(tooltip).toHaveCount(0);
  });
});
