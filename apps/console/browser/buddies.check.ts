import { expect } from "@playwright/test";
import { TOOLBAR_ITEMS, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

const languages = [
  { locale: "zh-CN", sections: ["Harness", "模型", "Router", "评价"], enabled: "启用" },
  { locale: "en-US", sections: ["Harnesses", "Models", "Router", "Evaluation"], enabled: "Enabled" },
];

for (const { locale, sections, enabled } of languages) {
  test.describe(`the Buddy page header in ${locale} at 1440px`, () => {
    test.use({ locale, viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });

    test("keeps one full-width header and a flush underline across all four sections", async ({ page }) => {
      await open(page, "#buddies/harness");
      const main = page.getByRole("main");
      const header = main.locator('[data-slot="buddy-header"]');
      const navigation = header.getByRole("navigation");
      const links = navigation.getByRole("link");
      await expect(navigation).toHaveCount(1);
      await expect(links).toHaveCount(4);
      expect(await links.evaluateAll(nodes => nodes.map(node => node.firstChild?.textContent))).toEqual(sections);
      await expect(header.getByRole("status")).toContainText(enabled);
      const baseline = (await header.boundingBox())!;
      const banner = (await page.getByRole("banner").first().boundingBox())!;
      expect(baseline.y).toBe(banner.y + banner.height);
      expect(baseline.height).toBe(36);
      expect(baseline.width).toBe(1440);
      const views = ["harness", "models", "router", "evaluation"];
      for (const [index, view] of views.entries()) {
        await links.nth(index).click();
        await expect(page).toHaveURL(new RegExp(`#buddies/${view}$`));
        await expect(main.getByRole("region", { name: sections[index], exact: true })).toBeVisible();
        expect(await header.boundingBox()).toEqual(baseline);
        await expectWholeAndApart(header, header.locator('nav > a, :scope > [role="status"]'));
        await expectTextUncut(header.locator('nav > a, :scope > [role="status"]'));
        const selected = links.nth(index);
        await expect(selected).toHaveAttribute("aria-current", "page");
        const geometry = await selected.evaluate(link => {
          const bounds = link.getBoundingClientRect();
          const text = document.createRange(); text.selectNode(link.firstChild!);
          const words = text.getBoundingClientRect();
          const underline = getComputedStyle(link, "::after");
          return { textMiddle: (words.top + words.bottom) / 2, linkMiddle: (bounds.top + bounds.bottom) / 2,
            underlineBottom: bounds.bottom - parseFloat(underline.bottom), height: parseFloat(underline.height) };
        });
        expect(Math.abs(geometry.textMiddle - geometry.linkMiddle)).toBeLessThanOrEqual(1);
        expect(geometry.underlineBottom).toBeCloseTo(baseline.y + baseline.height, 0);
        expect(geometry.height).toBe(2);
        if (view === "models") {
          const toolbar = main.locator('[data-slot="catalogue-toolbar"]');
          expect((await toolbar.boundingBox())!.y).toBe(baseline.y + baseline.height);
          await expectWholeAndApart(main, toolbar.locator(TOOLBAR_ITEMS));
        }
        await expectNoSidewaysScroll(page);
      }
      await links.nth(0).click();
      await expect(page).toHaveURL(/#buddies\/harness$/);
      expect(await header.boundingBox()).toEqual(baseline);
      await expect(main.getByRole("table")).toHaveCount(0);
    });
  });
}
