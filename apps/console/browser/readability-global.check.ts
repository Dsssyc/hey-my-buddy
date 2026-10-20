import { expect } from "@playwright/test";
import { contrastOf, open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const stoppingMacro = "macro-22222222-2222-4222-8222-222222222222";

test.describe("shared readability", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1280, height: 900 }, colorScheme: "dark", reducedMotion: "reduce" });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
  });

  test("dark tooltip and popover are legible floating surfaces", async ({ page }) => {
    await open(page, "#buddies");
    const fold = page.locator("[data-slot=fold-handle]").first();
    await fold.hover();
    const tooltip = page.locator("[data-slot=tooltip-content]");
    await expect(tooltip).toBeVisible();
    expect(await contrastOf(tooltip)).toBeGreaterThanOrEqual(4.5);
    const tooltipGround = await tooltip.evaluate(node => getComputedStyle(node).backgroundColor);
    const pageGround = await page.locator("body").evaluate(node => getComputedStyle(node).backgroundColor);
    const brightness = (rgb: string) => rgb.match(/\d+/g)!.slice(0, 3).map(Number).reduce((sum, channel) => sum + channel, 0);
    expect(brightness(tooltipGround)).toBeGreaterThan(brightness(pageGround));

    await page.goto(`#tasks/${macro}`);
    const stats = page.locator("[data-slot=macro-overview-facts] [data-slot=popover-trigger]");
    await stats.click();
    const popover = page.locator("[data-slot=popover-content]");
    await expect(popover).toBeVisible();
    expect(await contrastOf(popover.locator("[data-slot=time-stats-body]"))).toBeGreaterThanOrEqual(4.5);
    expect(await popover.evaluate(node => getComputedStyle(node).backgroundColor)).toBe(tooltipGround);
  });

  test("disclosures have neutral selection, pointer and keyboard focus", async ({ page }) => {
    await open(page, "#buddies");
    const fold = page.locator("[data-slot=fold-handle]").first();
    expect(await fold.evaluate(node => getComputedStyle(node).cursor)).toBe("pointer");
    const chevron = fold.locator("[data-slot=disclosure]");
    const text = await page.locator("body").evaluate(node => getComputedStyle(node).color);
    expect(await chevron.evaluate(node => getComputedStyle(node).color)).toBe(text);
    await fold.hover();
    await expect(fold).toHaveCSS("background-color", "rgb(52, 56, 61)");
    await page.mouse.move(0, 0);
    await page.keyboard.press("Tab");
    await fold.focus();
    await expect(page.locator("[data-slot=tooltip-content]")).toBeVisible();
    expect(await fold.evaluate(node => getComputedStyle(node).outlineStyle)).toBe("solid");
    await expect(fold).toHaveCSS("outline-color", "rgb(196, 200, 206)");
    await fold.click();
    await expect(chevron).toHaveAttribute("data-state", "closed");
    await fold.hover();
    await expect(fold).toHaveCSS("background-color", "rgb(18, 19, 21)");
    await fold.click();

    const model = page.locator("[data-slot=model-row]").first();
    await model.locator("button").first().click();
    await expect(model).toHaveClass(/bg-selection/);
    await expect(model).not.toHaveClass(/bg-live/);
  });

  test("stop facts appear on pointer and keyboard focus", async ({ page }) => {
    await open(page, `#tasks/${stoppingMacro}`);
    const status = page.locator("[data-slot=macro-stop-state]");
    await expect(status).toHaveText("正在停止");
    await expect(status).toHaveAttribute("aria-description", /停止未确认/);
    const detail = await status.getAttribute("aria-description");
    await status.hover();
    await expect(page.getByRole("tooltip")).toHaveText(detail!);
    await page.mouse.move(0, 0);
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await page.keyboard.press("Tab");
    await status.focus();
    await expect(page.getByRole("tooltip")).toHaveText(detail!);
  });
});
