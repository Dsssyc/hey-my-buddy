import { test as base, expect, type Locator, type Page } from "@playwright/test";

/**
 * Every check also fails on a page error or an error in the browser console,
 * which is where a refusal by the content security policy is reported.
 * https://playwright.dev/docs/test-fixtures
 */
export const test = base.extend({
  page: async ({ page }, use) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(String(error)));
    page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
    await use(page);
    expect(errors, "errors reported by the page").toEqual([]);
  },
});

/** Opens a console page and refuses anything that is not the synthetic preview. */
export async function open(page: Page, hash: string): Promise<void> {
  const response = await page.goto(hash);
  expect(response?.headers()["x-buddy-preview"]).toBe("synthetic-fixture-data");
  await page.getByRole("navigation").first().waitFor();
}

type Box = { name: string; left: number; top: number; right: number; bottom: number };

async function boxes(items: Locator): Promise<Box[]> {
  return items.evaluateAll(elements => elements.map(element => {
    const { left, top, right, bottom } = element.getBoundingClientRect();
    return { name: (element.textContent ?? "").trim().slice(0, 40) || element.tagName, left, top, right, bottom };
  }));
}

/** Every item lies inside the frame, and no two of them share a pixel. */
export async function expectWholeAndApart(frame: Locator, items: Locator): Promise<void> {
  const [outer] = await boxes(frame);
  const inner = await boxes(items);
  expect(inner.length).toBeGreaterThan(0);
  for (const box of inner) {
    expect(box.right - box.left, `${box.name} has no width`).toBeGreaterThan(0);
    expect(box.left >= outer.left - 0.5 && box.right <= outer.right + 0.5 && box.top >= outer.top - 0.5 && box.bottom <= outer.bottom + 0.5,
      `${box.name} leaves its frame: ${JSON.stringify(box)} outside ${JSON.stringify(outer)}`).toBe(true);
  }
  for (let first = 0; first < inner.length; first++) for (let second = first + 1; second < inner.length; second++) {
    const a = inner[first], b = inner[second];
    expect(a.right <= b.left + 0.5 || b.right <= a.left + 0.5 || a.bottom <= b.top + 0.5 || b.bottom <= a.top + 0.5,
      `${a.name} and ${b.name} overlap`).toBe(true);
  }
}

/** The text of every item is fully drawn: nothing is cut off or replaced by an ellipsis. */
export async function expectTextUncut(items: Locator): Promise<void> {
  const cut = await items.evaluateAll(elements => elements
    .filter(element => element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1)
    .map(element => (element.textContent ?? "").trim().slice(0, 40)));
  expect(cut, "text cut off").toEqual([]);
}

/** The page itself never scrolls sideways. */
export async function expectNoSidewaysScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "the page scrolls sideways").toBeLessThanOrEqual(0);
}
