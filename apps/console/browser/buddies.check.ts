import { expect } from "@playwright/test";
import { TOOLBAR_ITEMS, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

const languages = [
  { locale: "zh-CN", sections: ["目录", "角色"] },
  { locale: "en-US", sections: ["Catalog", "Roles"] },
];

for (const { locale, sections } of languages) for (const width of [1280, 390]) {
  test.describe(`the Buddy page navigation in ${locale} at ${width}px`, () => {
    test.use({ locale, viewport: { width, height: 844 } });

    test("places the section links in the catalogue toolbar and keeps every item whole", async ({ page }) => {
      await open(page, "#buddies/catalog");
      const navigation = page.getByRole("main").getByRole("navigation");
      await expect(navigation).toHaveCount(1);
      const links = navigation.getByRole("link");
      for (const [index, name] of sections.entries()) await expect(links.nth(index)).toContainText(name);
      await expectWholeAndApart(navigation, links);
      await expectTextUncut(links);
      await expectWholeAndApart(page.getByRole("main"), page.locator("[data-slot=catalogue-toolbar]").locator(TOOLBAR_ITEMS));
      await expectNoSidewaysScroll(page);
      const [bar, toolbar] = [await page.getByRole("banner").first().boundingBox(), await page.locator("[data-slot=catalogue-toolbar]").boundingBox()];
      expect(toolbar!.x).toBe(0);
      expect(toolbar!.width).toBe(width);
      expect(toolbar!.y).toBe(bar!.y + bar!.height);
    });

    test("returns from roles to the single catalogue address without a table selector", async ({ page }) => {
      await open(page, "#buddies/catalog");
      const navigation = page.getByRole("main").getByRole("navigation");
      await navigation.getByRole("link").nth(1).click();
      await expect(page).toHaveURL(/#buddies\/roles$/);
      await expect(navigation).toHaveCount(1);
      await navigation.getByRole("link").nth(0).click();
      await expect(page).toHaveURL(/#buddies\/catalog$/);
      await expect(navigation).toHaveCount(1);
      await expect(page.getByRole("main").getByRole("table")).toHaveCount(0);
    });
  });
}
