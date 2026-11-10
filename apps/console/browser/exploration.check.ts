import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, settle, test } from "./preview";
import { expect } from "@playwright/test";

const stage = process.env.HMB_EXPLORATION_STAGE;
const directory = process.env.HMB_EXPLORATION_SCREENSHOTS;
for (const words of [
  { locale: "zh-CN", roles: "Router", mode: "默认模式", quota: "探索名额", interval: "探索间隔微任务数", off: "关闭", on: "开启", position: "第 1 个 Router", mark: "探索", preferred: "首选：" },
  { locale: "en-US", roles: "Router", mode: "Default mode", quota: "Exploration quota", interval: "Micro tasks per exploration quota", off: "Off", on: "On", position: "Router #1", mark: "Exploration", preferred: "Preferred: " },
]) for (const theme of ["dark", "light"]) for (const width of [1440, 390]) {
  test.describe(`exploration ${words.locale} ${theme} ${width}px`, () => {
    test.use({ locale: words.locale, viewport: { width, height: width === 390 ? 844 : 900 } });
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    });
    test("Router settings remain readable and steady", async ({ page }) => {
      await open(page, "#buddies/router");
      const region = page.getByRole("region", { name: words.roles, exact: true });
      const section = region.getByRole("group", { name: stage === "before" ? words.mode : words.quota, exact: true });
      await section.evaluate(node => node.scrollIntoView({ block: "center" }));
      await settle(page);
      if (stage !== "before") {
        const input = section.getByRole("spinbutton", { name: words.interval });
        await expect(input).toHaveValue("5");
        if (width === 1440) {
          await expectWholeAndApart(section, section.locator(":scope > *"));
          await expectTextUncut(section.locator("p, button, input"));
        }
        await expect(input).toBeEnabled();
        if (width === 1440) {
          const before = await section.boundingBox();
          await section.getByRole("radio", { name: words.off, exact: true }).click();
          await expect(input).toBeDisabled();
          await section.getByRole("radio", { name: words.on, exact: true }).click();
          await expect(input).toBeEnabled();
          await settle(page);
          expect(await section.boundingBox()).toEqual(before);
        }
      }
      if (width === 1440) await expectNoSidewaysScroll(page);
      if (directory && words.locale === "zh-CN") {
        await mkdir(directory, { recursive: true });
        await page.screenshot({ path: join(directory, `settings-${theme}-${width}.png`), fullPage: true });
      }
    });
    test("routing shows exploration with the preferred buddy and Router position", async ({ page }) => {
      await open(page, "#tasks/macro-11111111-1111-4111-8111-111111111111/preview-run-a1/2");
      const pane = page.locator('[data-slot="detail-pane"]');
      await expect(pane).toContainText("界面工程");
      await settle(page);
      if (stage !== "before") {
        await expect(pane).toContainText(words.position);
        const mark = pane.getByText(words.mark, { exact: true });
        await expect(mark).toBeVisible();
        const before = await mark.boundingBox();
        await mark.hover();
        await expect(page.getByRole("tooltip")).toContainText(`${words.preferred}dsh / deepseek-official / deepseek-v4-pro / max`);
        expect(await mark.boundingBox()).toEqual(before);
        await page.mouse.move(0, 0);
        await expect(page.getByRole("tooltip")).toHaveCount(0);
      }
      await expectTextUncut(pane.locator('[data-slot="detail-band"] span'));
      await expectNoSidewaysScroll(page);
      if (directory && words.locale === "zh-CN") {
        await mkdir(directory, { recursive: true });
        await page.screenshot({ path: join(directory, `routing-${theme}-${width}.png`), fullPage: true });
      }
    });
  });
}
