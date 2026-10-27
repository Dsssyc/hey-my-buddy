import { expect } from "@playwright/test";
import { expectNoSidewaysScroll, expectWholeAndApart, open, settle, test } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";

// Match the retained before captures: the same fabricated macro, turn, viewport and clock.
for (const width of [1280, 390]) for (const theme of ["light", "dark"]) {
  test.describe(`batch-three integrated layout ${width}px ${theme}`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 900 }, reducedMotion: "reduce", colorScheme: theme as "light" | "dark" });
    test("keeps spaced rows, the fixed detail strip and independently collapsible Lead lanes", async ({ page }, testInfo) => {
      await page.clock.setFixedTime(new Date("2026-09-27T10:00:00Z"));
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/${A}`);
      const name = page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a1"]');
      await expect(name).toBeVisible();
      expect((await name.boundingBox())!.height).toBe(52);
      await expectWholeAndApart(name, name.locator(":scope > span"));
      await expect(page.locator('[data-slot=execution-bar]').first()).toHaveAttribute("height", "22");
      // Nothing sits beside the tracks: fitted, the canvas is exactly the scroll area's width.
      const viewport = (await page.locator('[data-slot=timeline-tracks]').boundingBox())!;
      const canvas = (await page.locator('[data-slot=timeline-canvas]').boundingBox())!;
      expect(Math.abs(canvas.width - viewport.width)).toBeLessThanOrEqual(1);
      await settle(page);
      await expectNoSidewaysScroll(page);
      await page.mouse.move(0, 0);
      await page.screenshot({ path: testInfo.outputPath(`batch3-after-macro-${width}-${theme}.png`) });

      await name.click();
      const strip = page.locator('[data-slot=detail-strip]');
      await expect(strip).toBeVisible();
      await expectWholeAndApart(strip, strip.locator(":scope > [data-slot=step-navigation], :scope > [data-slot=scroll-area]"));
      await expect(strip.locator('[aria-pressed=true]')).toHaveCount(1);
      await expect(page.locator('[data-slot=detail-pane]')).toContainText("回合 2");
      await settle(page);
      const beforeStrip = (await strip.boundingBox())!;
      const content = page.locator('[data-slot=detail-pane] [data-slot=scroll-area-viewport]');
      await content.evaluate(node => { node.scrollTop = 120; });
      expect((await strip.boundingBox())!.y).toBe(beforeStrip.y);
      if (width === 390) await expect(page.locator('[data-slot=task-names]')).toBeHidden();
      else await expect(page.locator('[data-slot=task-names]')).toBeVisible();
      await expectNoSidewaysScroll(page);
      await page.mouse.move(0, 0);
      await page.screenshot({ path: testInfo.outputPath(`batch3-after-detail-${width}-${theme}.png`) });

      await page.evaluate(hash => { window.location.hash = hash; }, `#tasks/${B}`);
      const header = page.locator('[data-slot=timeline-lane-lead][data-run-id="preview-run-b3"]');
      const lane = page.locator('[data-slot=timeline-lane]').filter({ has: header });
      await expect(lane.locator('[data-slot=timeline-row-name]')).toHaveCount(5);
      const leadName = header.locator('[data-slot=timeline-row-name]');
      expect((await leadName.boundingBox())!.height).toBe(60);
      expect((await lane.locator('[data-slot=timeline-row-name][data-run-id="preview-run-b4"]').boundingBox())!.height).toBe(52);
      // The Lead row ends in one divider that crosses the whole row, and its members sit one indent step inside it.
      const laneBox = (await lane.boundingBox())!;
      const headerBox = (await header.boundingBox())!;
      expect(Math.abs(headerBox.width - laneBox.width)).toBeLessThanOrEqual(1);
      expect(await header.evaluate(node => getComputedStyle(node).borderBottomWidth)).toBe("1px");
      expect(await leadName.evaluate(node => node.style.paddingLeft)).toBe("13px");
      await expect(lane.locator('[data-slot=timeline-row-name][data-run-id="preview-run-b4"]')).toHaveCSS("padding-left", "31px");
      // The lane's own numbers ride on the Lead row's hover: members, times and split usage.
      await leadName.hover();
      const tooltip = page.getByRole("tooltip");
      await expect(tooltip).toContainText("5 个微任务");
      await expect(tooltip).toContainText(/历时/);
      await expect(tooltip).toContainText(/执行/);
      await expect(tooltip).toContainText("输入 26.6k（含缓存 12.4k） · 输出 2.7k");
      await page.mouse.move(0, 0);
      const ordinary = page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-b10"]');
      await expect(ordinary).toHaveCount(1);
      const toggle = header.locator('[data-slot=lane-toggle]');
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await expect(lane.locator('[data-slot=timeline-row-name]')).toHaveCount(1);
      await expect(page).toHaveURL(new RegExp(`#tasks/${B}$`));
      await expect(page.locator('[data-slot=task-detail]')).toHaveCount(0);
      await toggle.click();
      await expect(lane.locator('[data-slot=timeline-row-name]')).toHaveCount(5);
      await header.scrollIntoViewIfNeeded();
      await settle(page);
      await expectNoSidewaysScroll(page);
      await page.mouse.move(0, 0);
      await page.screenshot({ path: testInfo.outputPath(`batch3-after-lanes-${width}-${theme}.png`) });
    });
  });
}
