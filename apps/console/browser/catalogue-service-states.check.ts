import { expect } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { expectNoSidewaysScroll, open, settle, test } from "./preview";

const shots = fileURLToPath(new URL("../../../tmp/redo-b2/", import.meta.url));
const CODEX = "codex / openai / gpt-5-codex";

for (const theme of ["light", "dark"]) for (const locale of ["zh-CN", "en-US"]) {
  test.describe(`catalog service states at 1440px in ${locale}, ${theme}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 1100 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    });

    test("shows provider identity, missing reads and profiles, service measurements and verified versions", async ({ page }) => {
      const zh = locale === "zh-CN";
      await open(page, "#buddies/catalog");
      const capture = async (name: string) => {
        await settle(page);
        await expectNoSidewaysScroll(page);
        await page.screenshot({ path: `${shots}${name}-${locale}-${theme}.png`, fullPage: true });
      };
      const pending = page.getByRole("button", { name: "zcode / zai / glm-5", exact: true });
      const missing = pending.getByText(zh ? "待确认" : "Pending", { exact: true });
      await expect(missing).toHaveAttribute("aria-description", zh ? /满一小时后复查.*无需操作/ : /one hour.*No action is needed/);
      await missing.hover();
      await expect(page.getByRole("tooltip")).toContainText("2026");
      await page.mouse.move(0, 0);
      const mini = page.getByRole("button", { name: "codex / openai / gpt-5-mini", exact: true });
      await expect(mini.locator("[data-slot=usage-mark]")).toHaveAttribute("aria-label", zh ? "已启用；尚未构建画像。" : "Enabled; no model profile yet.");
      await expect(mini).toContainText("— / 2");
      const dsh = page.getByRole("region", { name: "DSH", exact: true });
      await expect(dsh.locator("[data-slot=model-provider]")).toHaveText(["deepseek-official", "deepseek-partner", "deepseek-official"]);
      await expect(dsh.locator("[data-slot=harness-header] [data-slot=cooling-status]")).toHaveCount(1);
      await expect(dsh.locator("[data-slot=model-row] [data-slot=cooling-status]")).toHaveCount(0);
      await capture("catalog");

      await page.getByRole("button", { name: CODEX, exact: true }).click();
      const drawer = page.getByRole("region", { name: CODEX, exact: true });
      await expect(drawer.getByRole("meter")).toHaveAttribute("aria-valuenow", "1");
      await expect(drawer.locator("[data-slot=buddy-samples]")).toHaveCount(1);
      await expect(drawer.locator("[data-slot=buddy-panel] [data-slot=buddy-infrastructure-failures]")).toHaveCount(0);
      await expect(drawer.getByRole("note")).toHaveCount(0);
      await expect(drawer.locator("[data-slot=buddy-choice] [role=switch]")).toHaveCount(await drawer.locator("[data-slot=buddy-stop]").count());
      await capture("drawer");
      await drawer.getByRole("spinbutton").fill("3");
      await expect(drawer.getByRole("note")).toBeVisible();
      await capture("drawer-raised-limit");
      await drawer.getByRole("spinbutton").fill("2");
      await expect(drawer.getByRole("note")).toHaveCount(0);
      await page.getByRole("button", { name: CODEX, exact: true }).click();

      const details = page.getByRole("group", { name: zh ? "DSH 设置" : "DSH settings", exact: true });
      await expect(details).toContainText("0.2.2-rc.1");
      await expect(details.locator(":scope > [role=group]")).toHaveCount(2);
      const zcode = page.getByRole("group", { name: zh ? "ZCode 设置" : "ZCode settings", exact: true });
      await expect(zcode).toContainText(zh ? "未核对" : "Not verified");
      await expect(zcode).toContainText(zh ? "不能构建模型画像" : "cannot build model profiles");
      await capture("harness");
    });
  });
}
