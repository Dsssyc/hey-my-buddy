import { expect } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { expectNoSidewaysScroll, expectWholeAndApart, open, settle, test } from "./preview";

const drawings = fileURLToPath(new URL("../../../docs/design/console-frontend/", import.meta.url));
const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";

// Match the retained before captures: the same fabricated macro, turn, viewport and clock.
for (const width of [1280, 390]) for (const theme of ["light", "dark"]) {
  test.describe(`batch-three integrated layout ${width}px ${theme}`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 900 }, reducedMotion: "reduce", colorScheme: theme as "light" | "dark" });
    test("keeps compact rows, the fixed detail strip and independently collapsible Lead lanes", async ({ page }) => {
      await page.clock.setFixedTime(new Date("2026-09-27T10:00:00Z"));
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/${A}`);
      const name = page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a1"]');
      await expect(name).toBeVisible();
      expect((await name.boundingBox())!.height).toBe(44);
      await expectWholeAndApart(name, name.locator(":scope > span"));
      await expect(page.locator('[data-slot=execution-bar]').first()).toHaveAttribute("height", "22");
      await settle(page);
      await expectNoSidewaysScroll(page);
      await page.mouse.move(0, 0);
      await page.screenshot({ path: `${drawings}batch3-after-macro-${width}-${theme}.png` });

      await name.click();
      const strip = page.locator('[data-slot=detail-strip]');
      await expect(strip).toBeVisible();
      await expectWholeAndApart(strip, strip.locator(":scope > [data-slot=scroll-area], :scope > [data-slot=timeline-zoom]"));
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
      await page.screenshot({ path: `${drawings}batch3-after-detail-${width}-${theme}.png` });

      await page.evaluate(hash => { window.location.hash = hash; }, `#tasks/${B}`);
      const header = page.locator('[data-slot=timeline-lane-lead][data-run-id="preview-run-b3"]');
      const lane = page.locator('[data-slot=timeline-lane]').filter({ has: header });
      await expect(lane.locator('[data-slot=timeline-row-name]')).toHaveCount(5);
      expect((await header.locator('[data-slot=timeline-row-name]').boundingBox())!.height).toBe(60);
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
      await page.screenshot({ path: `${drawings}batch3-after-lanes-${width}-${theme}.png` });
    });
  });
}
