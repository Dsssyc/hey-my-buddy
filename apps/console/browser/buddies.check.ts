import { expect } from "@playwright/test";
import { expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

const languages = [
  { locale: "zh-CN", sections: ["目录", "角色"], tables: ["模型", "Harness"], quota: "额度接近上限：DSH" },
  { locale: "en-US", sections: ["Catalog", "Roles"], tables: ["Models", "Harnesses"], quota: "Quota near its limit: DSH" },
];

for (const { locale, sections, tables, quota } of languages) for (const width of [1440, 390]) {
  test.describe(`the Buddy page's navigation in ${locale} at ${width}px`, () => {
    test.use({ locale, viewport: { width, height: 844 } });

    test("runs edge to edge under the top bar and keeps every item whole and apart", async ({ page }) => {
      await open(page, "#buddies");
      const navigation = page.getByRole("main").getByRole("navigation");
      await expect(navigation).toHaveCount(2);
      const [sectionLinks, tableLinks] = [navigation.nth(0).getByRole("link"), navigation.nth(1).getByRole("link")];
      for (const [index, name] of sections.entries()) await expect(sectionLinks.nth(index)).toContainText(name);
      for (const [index, name] of tables.entries()) await expect(tableLinks.nth(index)).toContainText(name);
      await expectWholeAndApart(navigation.nth(0), sectionLinks);
      await expectWholeAndApart(navigation.nth(1), tableLinks);
      await expectTextUncut(navigation.getByRole("link"));
      await expectNoSidewaysScroll(page);
      const [bar, header] = [await page.getByRole("banner").first().boundingBox(), await navigation.nth(0).boundingBox()];
      expect(header!.x).toBe(0);
      expect(header!.width).toBe(width);
      expect(header!.y).toBe(bar!.y + bar!.height);
    });

    test("offers the two tables only inside the catalogue, each view at its own address", async ({ page }) => {
      await open(page, "#buddies");
      const navigation = page.getByRole("main").getByRole("navigation");
      await navigation.nth(1).getByRole("link").nth(1).click();
      await expect(page).toHaveURL(/#buddies\/harness$/);
      await navigation.nth(0).getByRole("link").nth(1).click();
      await expect(page).toHaveURL(/#buddies\/roles$/);
      await expect(navigation).toHaveCount(1);
      await navigation.nth(0).getByRole("link").nth(0).click();
      await expect(page).toHaveURL(/#buddies\/models$/);
      await expect(navigation).toHaveCount(2);
    });

    test("says what the mark on the harness table means, inside the window", async ({ page }) => {
      await open(page, "#buddies");
      const harness = page.getByRole("main").getByRole("navigation").nth(1).getByRole("link").nth(1);
      await harness.hover();
      await expect(page.getByRole("tooltip")).toHaveText(quota);
      await expectWholeAndApart(page.locator("body"), page.locator("[data-slot=tooltip-content]"));
    });
  });
}
