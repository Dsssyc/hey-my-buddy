import { expect, type Locator, type Page } from "@playwright/test";
import { open, test } from "./preview";

async function expectFailure(page: Page, region: Locator, text: string, code: string) {
  const failure = region.locator('[data-slot="router-failure"]').filter({ hasText: text }).first();
  await expect(failure).toHaveText(text);
  await expect(failure).toHaveAttribute("aria-description", code);
  await expect(region).not.toContainText(code);
  await failure.hover();
  await expect(page.getByRole("tooltip")).toHaveText(code);
  await page.mouse.move(0, 0);
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await page.keyboard.press("Tab");
  await failure.focus();
  await expect(failure).toBeFocused();
  await expect(page.getByRole("tooltip")).toHaveText(code);
  await page.keyboard.press("Escape");
}

for (const locale of ["zh-CN", "en-US"]) for (const theme of ["dark", "light"]) test.describe(`Router failure copy ${locale} ${theme}`, () => {
  test.use({ locale, viewport: { width: 1440, height: 1100 }, reducedMotion: "reduce" });
  test("explains native and unknown failures in all three jobs without exposing codes in record text", async ({ page }) => {
    const zh = locale === "zh-CN";
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await page.route("**/api/console", async route => {
      const response = await route.fetch(); const snapshot = await response.json();
      snapshot.routingHealth.routers[0].lastError.code = "native-prompt-refused";
      const failure = snapshot.routingHealth.recentFailures[0];
      snapshot.routingHealth.recentFailures = [
        { ...failure, code: "native-prompt-refused" },
        { ...failure, decisionId: "synthetic-unknown-failure", code: "future-native-failure" },
      ];
      const maintenance = snapshot.maintenance.runs[0];
      snapshot.maintenance.runs = ["native-prompt-refused", "future-native-failure"].map((code, index) => ({
        ...maintenance, maintenanceRunId: `synthetic-failure-${index}`, state: "failed", reason: { code, params: {} },
      }));
      const profile = snapshot.models.find((row: { latestProfileRun: unknown }) => row.latestProfileRun);
      snapshot.models = ["native-prompt-refused", "future-native-failure"].map((code, index) => ({
        ...profile, modelId: `synthetic-failure-model-${index}`, displayName: `Synthetic failure model ${index}`,
        latestProfileRun: { ...profile.latestProfileRun, profileRunId: `synthetic-failure-profile-${index}`, state: "failed",
          startedAt: `2026-11-10T0${2 - index}:00:00Z`, finishedAt: "2026-11-10T03:00:00Z", reason: { code, params: {} } },
      }));
      await route.fulfill({ response, json: snapshot });
    });
    const native = zh ? "harness 拒绝了任务提示" : "Harness refused the task prompt";
    const unknown = zh ? "本次执行失败，原因暂无法说明" : "This run failed for an unrecognized reason";
    await open(page, "#buddies/router/routing");
    await expectFailure(page, page.locator('[data-slot="routing-work"]'), native, "native-prompt-refused");
    await expectFailure(page, page.locator('[data-slot="routing-work"]'), unknown, "future-native-failure");

    const nav = page.getByRole("navigation", { name: zh ? "Router 的工作" : "Router work" });
    await nav.getByRole("link", { name: zh ? /^维护/ : /^Maintenance/ }).click();
    await expectFailure(page, page.locator('[data-slot="maintenance-records-region"]'), native, "native-prompt-refused");
    await expectFailure(page, page.locator('[data-slot="maintenance-records-region"]'), unknown, "future-native-failure");

    await nav.getByRole("link", { name: zh ? /^画像/ : /^Profiles/ }).click();
    const profiles = page.locator('[data-slot="profile-records"]');
    await expectFailure(page, profiles, native, "native-prompt-refused");
    await profiles.getByRole("button", { name: "Synthetic failure model 1", exact: true }).click();
    await expectFailure(page, profiles, unknown, "future-native-failure");
  });
});
