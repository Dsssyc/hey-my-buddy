import { expect } from "@playwright/test";
import { open, test } from "./preview";

const MACRO = "macro-22222222-2222-4222-8222-222222222222";

for (const [locale, recordedBy, conclusion] of [["zh-CN", "记录人", "Host 的结论"], ["en-US", "Recorded by", "Host's conclusion"]]) {
  test.describe(`structured actors in ${locale}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 1000 } });

    test("shows the Lead conclusion and nullable Host through the built console", async ({ page }) => {
      await open(page, `#tasks/${MACRO}/preview-run-b2`);
      const detail = page.locator("[data-slot=task-detail]");
      await expect(detail).toBeVisible();
      await page.locator("[data-slot=detail-rail] button").last().click();
      const section = page.locator("[data-slot=detail-pane]").getByRole("group", { name: conclusion });
      await expect(section).toContainText("Lead · preview-run-b1");
      await expect(section.getByText(recordedBy, { exact: true })).toBeVisible();
      await expect(page.locator("body")).not.toContainText("[object Object]");
    });
  });
}
