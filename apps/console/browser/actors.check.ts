import { expect } from "@playwright/test";
import { open, test } from "./preview";

for (const [locale, recordedBy] of [["zh-CN", "记录来源"], ["en-US", "Recorded by"]]) {
  test.describe(`structured actors in ${locale}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 1000 } });

    test("shows the Lead conclusion and nullable Host through the built console", async ({ page }) => {
      await open(page, "#tasks");
      await page.locator('[data-macro-task-id="macro-22222222-2222-4222-8222-222222222222"] button').click();
      await page.locator('.tl-label[data-key="row:preview-run-b2"]').dblclick();
      const conclusion = page.locator(".host-conclusion");
      await expect(conclusion).toBeVisible();
      await expect(conclusion).toContainText("Lead · preview-run-b1");
      await expect(conclusion.getByText(recordedBy, { exact: true })).toBeVisible();
      await page.locator(".record-facts > summary").click();
      await expect(page.locator(".record-facts")).toContainText("Lead · preview-run-b1");
      await expect(page.locator("body")).not.toContainText("[object Object]");
    });
  });
}
