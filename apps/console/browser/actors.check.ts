import { expect } from "@playwright/test";
import { open, test } from "./preview";

const MACRO = "macro-22222222-2222-4222-8222-222222222222";

for (const [locale, conclusion] of [["zh-CN", "Host 的结论"], ["en-US", "Host's conclusion"]]) {
  test.describe(`structured actors in ${locale}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 1000 } });

    test("shows the Lead conclusion and nullable Host through the built console", async ({ page }) => {
      await open(page, `#tasks/${MACRO}/preview-run-b2`);
      const detail = page.locator("[data-slot=task-detail]");
      await expect(detail).toBeVisible();
      await page.locator("[data-slot=detail-strip]").getByRole("button", { name: /^(结论|Conclusion) ·/ }).click();
      const section = page.locator("[data-slot=detail-pane]").getByRole("group", { name: conclusion });
      const title = page.locator("[data-slot=detail-pane] [data-slot=pane-title]");
      await expect(title).toContainText("Lead · preview-run-b1");
      await expect(title.locator("time")).toHaveCount(1);
      await expect(section).not.toContainText("Lead · preview-run-b1");
      await expect(section.locator("dt").filter({ hasText: /^(记录人|Recorded by)$/ })).toHaveCount(0);
      await expect(page.locator("body")).not.toContainText("[object Object]");
    });
  });
}
