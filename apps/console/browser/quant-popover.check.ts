import { expect, type Locator } from "@playwright/test";
import { expectNoSidewaysScroll, open, test } from "./preview";

const FLASH = "#buddies/evaluation/dsh/00000000-0000-4000-8000-000000000001/max";
const GLM_ID = "00000000-0000-4000-8000-000000000005";

async function layout(region: Locator) {
  return region.locator(":scope > *").evaluateAll(nodes => nodes.map(node => {
    const { x, y, width, height } = node.getBoundingClientRect();
    return { x, y, width, height };
  }));
}

for (const theme of ["dark", "light"] as const) {
  for (const width of [1440, 390]) test.describe(`evaluation count popover ${theme} ${width}px`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 1100 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme));

    test("opens complete sample details without shifting content and closes transiently", async ({ page }, testInfo) => {
      if (width === 1440) await page.emulateMedia({ reducedMotion: "no-preference" });
      await open(page, FLASH);
      const region = page.locator('[data-slot="domain-evaluation"]');
      const trigger = region.locator('[data-slot="quant-bar-trigger"]').first();
      const panel = page.locator('[data-slot="quant-bar-detail"]');
      await trigger.scrollIntoViewIfNeeded();
      const before = await layout(region);
      await trigger.click();
      await expect(panel).toBeVisible();
      await expect(panel).toHaveRole("dialog");
      await expect(panel).toHaveClass(/popover-reveal/);
      await expect(panel).toHaveAttribute("data-reveal-ready", "true");
      for (const text of ["一次通过 2", "返工后成功 1", "失败 1", "返工轮数 1", "中位数", "交付", "输入", "缓存", "输出"]) {
        await expect(panel).toContainText(text);
      }
      if (width === 1440) {
        await expect(panel).toHaveCSS("transition-property", "--reveal-progress");
        await expect(panel).toHaveCSS("transition-duration", "0.2s");
        await expect.poll(() => panel.evaluate(node => getComputedStyle(node).getPropertyValue("--reveal-progress").trim())).toBe("1");
      }
      expect(await layout(region)).toEqual(before);
      await expect(region.locator('[data-slot="quant-bar-detail"]')).toHaveCount(0);
      expect(await panel.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
      await expectNoSidewaysScroll(page);
      await page.screenshot({ path: testInfo.outputPath("sample-popover.png") });
      await page.keyboard.press("Escape");
      await expect(panel).toHaveCount(0);
      await expect(trigger).toBeFocused();
      await trigger.click();
      await expect(panel).toBeVisible();
      await trigger.click();
      await expect(panel).toHaveCount(0);
      await trigger.click();
      await expect(panel).toBeVisible();
      await page.getByRole("banner").locator("time:visible").click();
      await expect(panel).toHaveCount(0);
      await trigger.click();
      await expect(panel).toBeVisible();
      await page.reload();
      await expect(trigger).toHaveAttribute("aria-expanded", "false");
      await expect(panel).toHaveCount(0);
    });
  });

  test.describe(`profile completion usage popover ${theme}`, () => {
    test.use({ locale: "en-US", viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme));

    test("anchors all usage parts and total outside the completion notice", async ({ page }, testInfo) => {
      let started = false;
      await page.route("**/api/console", async route => {
        const response = await route.fetch();
        const snapshot = await response.json();
        snapshot.configuration.profileUnavailableReason = null;
        if (started) {
          const model = snapshot.models.find((row: { modelId: string }) => row.modelId === GLM_ID);
          model.latestProfileRun = { ...model.latestProfileRun, profileRunId: "synthetic-profile-failure-new", state: "failed" };
        }
        await route.fulfill({ response, json: snapshot });
      });
      await page.route("**/api/command", async route => {
        const body = route.request().postDataJSON();
        if (body.operation !== "profile_start") return route.fallback();
        started = true;
        await route.fulfill({ json: { ok: true, result: { status: "running", profileRunId: "synthetic-profile-failure-new", runId: "synthetic-profile-task-failed", buddyId: "dsh:deepseek-partner:deepseek-flash:max", usesQuota: true } } });
      });
      await open(page, `#buddies/evaluation/zcode/${GLM_ID}/high/profile`);
      await page.locator('[data-slot="model-portrait"]').getByRole("button", { name: "Build profile" }).click();
      await page.getByRole("dialog").getByRole("button", { name: "Confirm build" }).click();
      const notice = page.getByRole("status").filter({ hasText: "glm-5" });
      const trigger = notice.locator('[data-slot="quant-bar-trigger"]');
      const panel = page.locator('[data-slot="quant-bar-detail"]');
      await expect(trigger).toHaveAttribute("aria-expanded", "false");
      const before = await layout(page.locator('[data-slot="evaluation-detail"]'));
      const noticeBefore = await notice.boundingBox();
      await trigger.click();
      await expect(panel).toBeVisible();
      await expect(panel).toHaveRole("dialog");
      await expect(panel).toHaveClass(/popover-reveal/);
      await expect(panel).toHaveAttribute("data-reveal-ready", "true");
      await expect(panel).toContainText("Cached input 6,144; Other input 15,536; Output 2,460; Total usage 24,140");
      await page.screenshot({ path: testInfo.outputPath("usage-popover.png") });
      await expect(notice.locator('[data-slot="quant-bar-detail"]')).toHaveCount(0);
      expect(await layout(page.locator('[data-slot="evaluation-detail"]'))).toEqual(before);
      expect(await notice.boundingBox()).toEqual(noticeBefore);
      await page.keyboard.press("Escape");
      await expect(panel).toHaveCount(0);
      await expect(trigger).toBeFocused();
      await trigger.click();
      await expect(panel).toBeVisible();
      await trigger.click();
      await expect(panel).toHaveCount(0);
      await trigger.click();
      await expect(panel).toBeVisible();
      await page.getByRole("banner").locator("time:visible").click();
      await expect(panel).toHaveCount(0);
      await trigger.click();
      await expect(panel).toBeVisible();
      await page.reload();
      await expect(panel).toHaveCount(0);
      await expect(notice).toHaveCount(0);
    });
  });
}
