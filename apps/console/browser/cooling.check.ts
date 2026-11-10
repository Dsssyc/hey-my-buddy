import { expect } from "@playwright/test";
import { expectNoSidewaysScroll, open, settle, test } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";

for (const width of [1280, 390]) for (const theme of ["light", "dark"] as const) {
  test.describe(`cooling evidence ${width}px ${theme}`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 900 }, reducedMotion: "reduce" });
    test("keeps each provider cooldown and retry in the header", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, "#buddies/catalog");
      const section = page.locator("[data-slot=harness-section][aria-label=DSH]");
      const header = section.locator("[data-slot=harness-header]");
      const provider = header.locator("[data-slot=header-provider]").filter({ hasText: "deepseek-official" });
      await expect(provider.locator("[data-slot=cooling-status]")).toContainText("冷却中");
      await expect(provider.getByRole("button", { name: "开放重试" })).toHaveCount(1);
      await expect(header.locator("button button")).toHaveCount(0);
      await expect(section.locator("[data-slot=model-row] [data-slot=cooling-status]")).toHaveCount(0);
      const details = page.locator("[data-slot=harness-sidebar] [data-slot=harness-card]").filter({ has: page.locator("#harness-title-dsh") }).locator("[data-slot=harness-details]");
      await expect(details.locator(":scope > [role=group]")).toHaveCount(2);
      await expect(details.getByRole("button", { name: "开放重试" })).toHaveCount(0);
      await expectNoSidewaysScroll(page);
    });
    test("shows a cooling boundary, a rerouted success, and a mid-turn quota failure", async ({ page }, testInfo) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/${A}/preview-run-a4`);
      const pane = page.locator("[data-slot=detail-pane]");
      await expect(pane).toContainText("所有候选均在冷却中");
      await pane.getByRole("button", { name: "提交时的约束" }).click();
      await expect(pane).toContainText("冷却中的候选 2 个");
      await settle(page);
      await expectNoSidewaysScroll(page);
      await page.screenshot({ path: testInfo.outputPath(`task-cooling-${width}-${theme}.png`) });
      await page.evaluate(hash => { window.location.hash = hash; }, `#tasks/${B}/preview-run-b9`);
      await page.locator("[data-slot=detail-strip] [data-key='span:execution:att-b9-1']").click();
      await expect(pane).toContainText("模型开始前被拒绝");
      await expect(pane).toContainText("已改派");
      await expect(page.locator("[data-slot=detail-status]")).toHaveText("已验收");
      await settle(page);
      await expectNoSidewaysScroll(page);
      await page.screenshot({ path: testInfo.outputPath(`task-rerouted-${width}-${theme}.png`) });
      await page.evaluate(hash => { window.location.hash = hash; }, `#tasks/${B}/preview-run-b2`);
      await expect(pane).toContainText("额度耗尽");
      await page.screenshot({ path: testInfo.outputPath(`task-quota-failure-${width}-${theme}.png`) });
    });
  });
}
