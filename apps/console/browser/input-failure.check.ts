import { expect } from "@playwright/test";
import { contrastOf, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

const MACRO = "macro-55555555-5555-4555-8555-555555555555";

for (const locale of ["zh-CN", "en-US"]) for (const width of [390, 1280]) for (const known of [true, false]) {
  const suffix = known ? "input-preflight" : "input-native";
  test.describe(`${suffix} ${locale} ${width}px`, () => {
    test.use({ locale, viewport: { width, height: 1000 }, reducedMotion: "reduce" });
    test("shows one rejection on the Host wait and its turn, with readable measured parts", async ({ page }, testInfo) => {
      await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
      await open(page, `#tasks/${MACRO}/preview-run-${suffix}`);
      const pane = page.locator("[data-slot=detail-pane]");
      const facts = pane.locator("[data-slot=input-failure]");
      const stage = known ? locale === "zh-CN" ? "本地核对拒收" : "Rejected by local preflight"
        : locale === "zh-CN" ? "原生拒收" : "Rejected by native admission";
      await expect(pane.getByRole("heading").first()).toHaveText(locale === "zh-CN" ? "决定" : "Decision");
      await expect(facts).toHaveCount(1);
      await expect(facts).toContainText(stage);
      await expect(facts).toContainText(known ? "1,048,576" : locale === "zh-CN" ? "未取得数值" : "No numeric value obtained");
      await expect(facts.locator("tbody tr")).toHaveCount(6);
      await expect(facts.locator("tfoot")).toContainText("1,048,577");
      if (!known) {
        await expect(facts.locator("tfoot")).toContainText("2,097,154");
        await expect(facts).toContainText(locale === "zh-CN" ? "原生报告上限" : "Native reported limit");
      }
      const checkGeometry = async () => {
        await expectNoSidewaysScroll(page);
        await expectTextUncut(facts.locator("th, td, dt, dd, p"));
        await expectWholeAndApart(facts, facts.locator(":scope > *"));
        expect(await pane.locator("[data-slot=scroll-area-viewport]").evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(0);
        for (const item of await facts.locator("p, th, td, dt, dd").all()) expect(await contrastOf(item)).toBeGreaterThanOrEqual(4.5);
      };
      await checkGeometry();
      await page.screenshot({ path: testInfo.outputPath(`${suffix}-${locale}-${width}-host-dark.png`) });
      await page.locator(`[data-slot=detail-strip] [data-key="span:execution:att-${suffix}"]`).click();
      await expect(pane.getByRole("heading").first()).toHaveText(locale === "zh-CN" ? "回合 1" : "Turn 1");
      await expect(pane.locator("[data-slot=pane-title] > p")).toHaveText(stage);
      await expect(facts).toHaveCount(1);
      await expect(facts).toContainText(stage);
      await checkGeometry();
      for (const button of await page.locator("[data-slot=copyable-identifier]").all()) {
        const box = (await button.boundingBox())!;
        expect(box.height).toBeGreaterThanOrEqual(24);
        expect(box.width).toBeGreaterThanOrEqual(24);
      }
      await page.screenshot({ path: testInfo.outputPath(`${suffix}-${locale}-${width}-turn-dark.png`) });
    });
  });
}
