import { expect } from "@playwright/test";
import { expectNoSidewaysScroll, expectTextUncut, open, test } from "./preview";

for (const locale of ["en-US", "zh-CN"]) for (const theme of ["light", "dark"] as const) {
  test.describe(`Router profile records ${locale} ${theme}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme));

    test("shows completed and failed runs with tool outcomes", async ({ page }) => {
      await open(page, "#buddies/router/profiles");
      const records = page.locator("[data-slot=profile-records]");
      const completed = records.getByRole("button", { name: "gpt-5-codex" });
      await completed.click();
      const completedDetail = records.locator('tr:has(> td > button:has-text("gpt-5-codex")) + tr');
      await expect(completedDetail).toContainText("900 / 3,300 / 650");
      const tools = completedDetail.getByRole("table").first();
      await expect(tools).toContainText("web_search");
      await expect(tools).toContainText("web_fetch");
      await expect(tools).toContainText(locale === "en-US" ? "All failed" : "全部失败");
      await expect(tools.getByRole("rowheader", { name: /web_fetch/ }).locator("[data-slot=state-mark]")).toHaveAttribute("data-kind", "fail");
      await expectTextUncut(tools.getByRole("rowheader"));
      const failed = records.getByRole("button", { name: "glm-5" });
      await expect(failed).toHaveAttribute("aria-expanded", "true");
      const failedDetail = records.locator('tr:has(> td > button:has-text("glm-5")) + tr');
      await expect(failedDetail).toContainText(locale === "en-US" ? "All failed" : "全部失败");
      await expect(failedDetail.getByRole("row", { name: /web_fetch/ })).toContainText("0 / 8 / 0");
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
      await records.getByRole("button", { name: "gpt-5-codex" }).click();
      let detail = records.locator('tr:has(> td > button:has-text("gpt-5-codex")) + tr');
      await expect(detail).toContainText(locale === "en-US" ? "No tool calls recorded" : "没有工具调用记录");
      toolSummary = [{ toolName: "web_fetch", calls: 3, failed: 1, unknown: 1 }];
      await page.reload();
      await records.getByRole("button", { name: "gpt-5-codex" }).click();
      detail = records.locator('tr:has(> td > button:has-text("gpt-5-codex")) + tr');
      const toolRow = detail.getByRole("row", { name: /web_fetch/ });
      await expect(toolRow).toContainText("1 / 1 / 1");
      await expect(toolRow.locator("[data-slot=state-mark]")).toHaveAttribute("data-kind", "fail");
    });
  });
}
