import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { open, test, expectNoSidewaysScroll } from "./preview";

for (const theme of ["dark", "light"]) for (const width of [1440, 390]) {
  test.describe(`maintenance ${theme} ${width}px`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 1200 }, reducedMotion: "reduce" });
    test("shows maintenance history and domain hierarchy and opens the eligible buddy picker", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, "#buddies/router/maintenance");
      const plane = page.locator('[data-slot="maintenance-plane"]');
      await expect(plane.locator('[data-slot="maintenance-record"]').first().locator('[aria-label="完成"]')).toBeVisible();
      await expect(plane).toContainText("界面工程");
      await plane.getByRole("button", { name: "全部展开" }).click();
      await expect(plane).toContainText("路由判断");
      await plane.evaluate(node => node.scrollIntoView({ block: "start" }));
      if (width === 1440) await expectNoSidewaysScroll(page);
      const interval = plane.getByRole("spinbutton", { name: "维护间隔" });
      await expect(interval).toHaveValue("3600");
      const destination = process.env.HMB_ROUTE_SCREENSHOTS;
      if (destination) { await mkdir(destination, { recursive: true }); await page.screenshot({ path: join(destination, `maintenance-${theme}-${width}.png`), fullPage: true }); }
      await plane.getByRole("button", { name: "选择 buddy" }).click();
      const picker = page.getByRole("dialog");
      await expect(picker).toBeVisible();
      await expect(picker.getByRole("button", { name: /dsh \/ deepseek-official \/ deepseek-flash \/ max/ })).toBeVisible();
      if (width === 1440) await expectNoSidewaysScroll(page);
      await page.keyboard.press("Escape");
      await plane.getByRole("treeitem", { name: /界面工程/ }).hover();
      await plane.getByRole("button", { name: "改名" }).click();
      await expect(page.getByRole("dialog").getByLabel("名称")).toBeVisible();
      await expect(page.getByRole("dialog").getByLabel("定义")).toBeVisible();
      await page.keyboard.press("Escape");
    });
  });
}
