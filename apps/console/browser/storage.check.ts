import { expect } from "@playwright/test";
import { expectTextUncut, open, test } from "./preview";

for (const width of [1440, 390]) test.describe(`the upgrade rollback archive at ${width}px`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 844 } });

  test("keeps the archive's purpose readable beside its size and protection", async ({ page }) => {
    await page.route("**/api/command", async route => {
      if (route.request().postDataJSON().operation !== "storage_plan") return route.fallback();
      const now = Date.now();
      await route.fulfill({ json: { ok: true, result: {
        planId: "synthetic-storage-plan",
        createdAt: new Date(now).toISOString(), expiresAt: new Date(now + 600_000).toISOString(),
        categories: [{ id: "backup", label: "上次升级之前的看板", bytes: 5_242_880,
          reclaimableBytes: 0, count: 1, eligibleCount: 0, reasons: ["durable-record"] }],
        candidates: [], orphanProcesses: [],
      } } });
    });
    await open(page, "#settings");
    const storage = page.getByRole("region", { name: "存储", exact: true });
    await storage.getByRole("button", { name: "检查占用", exact: true }).click();
    const row = storage.getByRole("row").filter({ hasText: "上次升级之前的看板" });
    await expect(row.getByRole("rowheader")).toHaveText("上次升级之前的看板");
    await expect(row.getByRole("cell").nth(0)).toHaveText("5.0 MB");
    await expect(row.getByRole("cell").nth(1)).toHaveText("—");
    await expectTextUncut(row.locator("th, td, button"));
  });
});
