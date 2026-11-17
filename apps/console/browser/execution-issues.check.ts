import { expect } from "@playwright/test";
import { contrastOf, expectNoSidewaysScroll, expectTextUncut, open, test } from "./preview";

test.use({ headless: true });

const macro = "macro-11111111-1111-4111-8111-111111111111";
const run = "preview-run-a3";

for (const locale of ["zh-CN", "en-US"]) for (const width of [390, 1280]) {
  test.describe(`execution issues ${locale} ${width}`, () => {
    test.use({ locale, viewport: { width, height: 900 }, reducedMotion: "reduce" });
    for (const finalizing of [false, true]) {
      test(`shows ${finalizing ? "unconfirmed finalization" : "a running connection issue"} without clipping`, async ({ page }) => {
        await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
        await page.route(`**/api/tasks/${run}`, async route => {
          const response = await route.fetch();
          const value = await response.json();
          // The preview has no execution issue stock; supply the current API's recorded facts.
          value.activity = { phase: "tool-running", toolName: "Bash", observedAt: "2026-09-26T11:00:19.499Z",
            lastToolActivityAt: "2026-09-26T11:00:19.499Z", counts: { modelTurns: 2, toolCalls: 3 } };
          value.selectedAttempt.executionState = finalizing ? "finalizing" : "executing";
          value.selectedAttempt.shutdownConfirmed = false;
          value.selectedAttempt.issues = [{ stage: finalizing ? "shutdown" : "connection", code: finalizing ? "EXECUTION_UNKNOWN" : "CONNECTION_LOST",
            message: finalizing ? "Native session identity is no longer verifiable." : "Native connection closed during recovery.", observedAt: "2026-09-26T11:00:19.499Z" }];
          await route.fulfill({ response, json: value });
        });
        await open(page, `#tasks/${macro}/${run}`);
        await page.locator('[data-slot=detail-strip] [data-key="span:execution:att-a3-1"]').click();
        const pane = page.locator('[data-slot=detail-pane]');
        const issues = pane.getByRole("group", { name: locale === "zh-CN" ? "执行问题" : "Execution issues" });
        await expect(issues).toBeVisible();
        await expect(issues).toContainText(finalizing ? "Native session identity is no longer verifiable." : "Native connection closed during recovery.");
        await expect(issues).not.toContainText("EXECUTION_UNKNOWN");
        await expect(issues.locator("time")).toHaveAttribute("datetime", "2026-09-26T11:00:19.499Z");
        const state = finalizing ? locale === "zh-CN" ? "正在确认停止" : "Confirming stop" : locale === "zh-CN" ? "进行中" : "Running";
        await expect(page.locator('[data-slot=detail-status]')).toHaveText(state);
        await expect(page.locator('[data-slot=detail-status] [data-slot=state-mark]')).toHaveAttribute("data-kind", "live");
        await expect(pane.locator('[data-slot=pane-title]')).toContainText(state);
        await expect(pane.locator('[data-slot=detail-live]')).toHaveCount(finalizing ? 0 : 1);
        if (!finalizing) await expect(pane.locator('[data-slot=detail-live]')).toContainText("Bash");
        await expectTextUncut(issues.locator("p, time"));
        await expectNoSidewaysScroll(page);
        for (const item of await issues.locator("li > p").all()) expect(await contrastOf(item)).toBeGreaterThanOrEqual(4.5);
        expect(await pane.locator('[data-slot=scroll-area-viewport]').evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(0);
      });
    }
  });
}
