import { expect } from "@playwright/test";
import { expectNoSidewaysScroll, expectTextUncut, open, test } from "./preview";

for (const locale of ["en-US", "zh-CN"]) for (const theme of ["light", "dark"] as const) {
  test.describe(`Router profile records ${locale} ${theme}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme));

    test("shows completed and failed runs with tool outcomes", async ({ page }) => {
      await open(page, "#buddies/router/profiles");
      const records = page.locator("[data-slot=profile-records]");
      const completed = records.getByRole("button", { name: "gpt-5-codex", exact: true });
      await completed.click();
      const completedDetail = records.locator('tr:has(> td > button:has-text("gpt-5-codex")) + tr');
      for (const bar of await completedDetail.locator('[data-slot="quant-bar-trigger"]').all()) {
        await expect(bar).toHaveAttribute("aria-expanded", "false");
        await bar.click();
      }
      await expect(completedDetail).toContainText(locale === "en-US" ? "Cached input 900; Other input 3,300; Output 650" : "缓存输入 900; 其他输入 3,300; 输出 650");
      const tools = completedDetail.getByRole("table").first();
      await expect(tools).toContainText("web_search");
      await expect(tools).toContainText("web_fetch");
      await expect(tools).toContainText(locale === "en-US" ? "All failed" : "全部失败");
      await expect(tools.getByRole("rowheader", { name: /web_fetch/ }).locator("[data-slot=state-mark]")).toHaveAttribute("data-kind", "fail");
      await expectTextUncut(tools.getByRole("rowheader"));
      const failed = records.getByRole("button", { name: "glm-5", exact: true });
      await expect(failed).toHaveAttribute("aria-expanded", "true");
      const failedDetail = records.locator('tr:has(> td > button:has-text("glm-5")) + tr');
      await expect(failedDetail).toContainText(locale === "en-US" ? "All failed" : "全部失败");
      const failedTool = failedDetail.getByRole("row", { name: /web_fetch/ });
      await failedTool.locator('[data-slot="quant-bar-trigger"]').click();
      await expect(failedTool).toContainText(locale === "en-US" ? "Succeeded 0 · Failed 8 · Unknown 0" : "成功 0 · 失败 8 · 未知 0");
      await expectNoSidewaysScroll(page);
    });

    test("marks partial tool failures and an empty tool summary", async ({ page }) => {
      let toolSummary: { toolName: string; calls: number; failed: number; unknown: number }[] = [];
      await page.route("**/api/console", async route => {
        const response = await route.fetch();
        const snapshot = await response.json();
        const model = snapshot.models.find((row: { displayName: string }) => row.displayName === "gpt-5-codex");
        model.latestProfileRun.toolSummary = toolSummary;
        await route.fulfill({ response, json: snapshot });
      });
      await open(page, "#buddies/router/profiles");
      const records = page.locator("[data-slot=profile-records]");
      await records.getByRole("button", { name: "gpt-5-codex", exact: true }).click();
      let detail = records.locator('tr:has(> td > button:has-text("gpt-5-codex")) + tr');
      await expect(detail).toContainText(locale === "en-US" ? "No tool calls recorded" : "没有工具调用记录");
      toolSummary = [{ toolName: "web_fetch", calls: 3, failed: 1, unknown: 1 }];
      await page.reload();
      await records.getByRole("button", { name: "gpt-5-codex", exact: true }).click();
      detail = records.locator('tr:has(> td > button:has-text("gpt-5-codex")) + tr');
      const toolRow = detail.getByRole("row", { name: /web_fetch/ });
      const trigger = toolRow.locator('[data-slot="quant-bar-trigger"]');
      await expect(trigger).toHaveAttribute("aria-expanded", "false");
      await expect(trigger.locator("[data-slot=state-mark]")).toHaveAttribute("data-kind", "fail");
      await expect(trigger.locator('[data-tone="green"]')).toHaveCount(1);
      await expect(trigger.locator('[data-tone="pink"]')).toHaveCount(1);
      await expect(trigger.locator('[data-tone="gray"]')).toHaveCount(1);
      await trigger.press("Enter");
      await expect(toolRow).toContainText(locale === "en-US" ? "Succeeded 1 · Failed 1 · Unknown 1" : "成功 1 · 失败 1 · 未知 1");
    });
  });
}
