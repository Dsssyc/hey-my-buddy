import { expect } from "@playwright/test";
import { contrastOf, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

const languages = [
  { locale: "zh-CN", tabs: ["任务", "Buddy", "设置"], pending: "等待 Host 决定的微任务" },
  { locale: "en-US", tabs: ["Tasks", "Buddies", "Settings"], pending: "Micro tasks waiting for their Host's decision" },
];

for (const { locale, tabs, pending } of languages) for (const width of [1440, 390]) {
  test.describe(`the top bar in ${locale} at ${width}px`, () => {
    test.use({ locale, viewport: { width, height: 844 } });

    test("keeps every item whole and apart", async ({ page }) => {
      await open(page, "#tasks");
      const bar = page.getByRole("banner").first();
      const links = bar.getByRole("navigation").getByRole("link");
      await expect(links).toHaveCount(3);
      for (const [index, name] of tabs.entries()) await expect(links.nth(index)).toContainText(name);
      await expectWholeAndApart(bar, bar.locator(":scope > *"));
      await expectWholeAndApart(bar.getByRole("navigation"), links);
      await expectTextUncut(bar.locator("a, p"));
      await expectNoSidewaysScroll(page);
    });

    test("says what the count on a tab means, on hover and on focus, inside the window", async ({ page }) => {
      await open(page, "#tasks");
      const tasks = page.getByRole("banner").first().getByRole("navigation").getByRole("link").first();
      const hint = page.getByRole("tooltip");
      await tasks.hover();
      await expect(hint).toHaveText(pending);
      await expectWholeAndApart(page.locator("body"), page.locator("[data-slot=tooltip-content]"));
      // The hint waits for the pointer to travel, so that it can be reached and read.
      await page.mouse.move(0, 400, { steps: 4 });
      await expect(hint).toHaveCount(0);
      await tasks.focus();
      await expect(hint).toHaveText(pending);
    });
  });
}

// The bar keeps the logo's ink in both themes, so everything on it has to be readable on that ink.
for (const theme of ["light", "dark"]) test(`the top bar is dark and readable in the ${theme} theme`, async ({ page }) => {
  await open(page, "#tasks");
  await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
  const bar = page.getByRole("banner").first();
  expect(await bar.evaluate(node => getComputedStyle(node).backgroundColor)).toBe(theme === "light" ? "rgb(31, 42, 36)" : "rgb(12, 15, 14)");
  // A mark is a dot without text; what is measured is whatever carries words.
  const texts = bar.locator("nav a, nav a > span:not(.sr-only):not([aria-hidden]), time");
  expect(await texts.count()).toBeGreaterThanOrEqual(5);
  for (const text of await texts.all()) {
    expect(await contrastOf(text), await text.textContent() ?? "").toBeGreaterThanOrEqual(4.5);
  }
});

test.describe("the time in the top bar", () => {
  test.use({ locale: "zh-CN" });
  test("says what it is the time of", async ({ page }) => {
    await open(page, "#tasks");
    await page.getByRole("banner").first().locator("time").hover();
    await expect(page.getByRole("tooltip")).toHaveText("核对时间");
  });
});
