import { expect } from "@playwright/test";
import { TOOLBAR_ITEMS, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

const languages = [
  { locale: "zh-CN", sections: ["目录", "Router", "评价"], enabled: "启用" },
  { locale: "en-US", sections: ["Catalog", "Router", "Evaluation"], enabled: "Enabled" },
];

for (const { locale, sections, enabled } of languages) {
  test.describe(`the Buddy page header in ${locale} at 1440px`, () => {
    test.use({ locale, viewport: { width: 1440, height: 900 } });

    test("keeps one full-width header at the same height across the directory and Router", async ({ page }) => {
      await open(page, "#buddies/catalog");
      const main = page.getByRole("main");
      const header = main.locator('[data-slot="buddy-header"]');
      const navigation = header.getByRole("navigation");
      const links = navigation.getByRole("link");
      const summary = header.getByRole("status");
      await expect(navigation).toHaveCount(1);
      await expect(links).toHaveCount(3);
      for (const [index, name] of sections.entries()) await expect(links.nth(index)).toContainText(name);
      await expect(summary).toContainText(enabled);
      await expectWholeAndApart(header, header.locator('nav > a, :scope > [role="status"]'));
      await expectTextUncut(header.locator('nav > a, :scope > [role="status"]'));
      await expectNoSidewaysScroll(page);

      const banner = (await page.getByRole("banner").first().boundingBox())!;
      const catalogHeader = (await header.boundingBox())!;
      const toolbar = (await main.locator('[data-slot="catalogue-toolbar"]').boundingBox())!;
      expect(catalogHeader.y).toBe(banner.y + banner.height);
      expect(catalogHeader.height).toBe(48);
      expect(catalogHeader.width).toBe(1440);
      expect(toolbar.y).toBe(catalogHeader.y + catalogHeader.height);
      await expectWholeAndApart(main, main.locator('[data-slot="catalogue-toolbar"]').locator(TOOLBAR_ITEMS));

      await links.nth(1).click();
      await expect(page).toHaveURL(/#buddies\/router$/);
      await expect(main.getByRole("region", { name: "Router" })).toBeVisible();
      await expect(navigation).toHaveCount(1);
      const routerHeader = (await header.boundingBox())!;
      expect(routerHeader.y).toBe(catalogHeader.y);
      expect(routerHeader.height).toBe(catalogHeader.height);
      expect(routerHeader.width).toBe(catalogHeader.width);

      await links.nth(2).click();
      await expect(page).toHaveURL(/#buddies\/evaluation$/);
      await expect(main.locator('[data-slot="evaluation-list"]')).toBeVisible();
      expect((await header.boundingBox())!.height).toBe(catalogHeader.height);

      await links.nth(0).click();
      await expect(page).toHaveURL(/#buddies\/catalog$/);
      await expect(navigation).toHaveCount(1);
      expect((await header.boundingBox())!.height).toBe(catalogHeader.height);
      await expect(main.getByRole("table")).toHaveCount(0);
    });
  });
}
