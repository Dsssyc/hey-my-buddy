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
  expect(response?.headers()["x-hey-my-buddy-preview"]).toBe("synthetic-fixture-data");
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

/**
 * Resolves when every transition and animation that has an end has ended, so that
 * boxes and colours are read at rest and never in the middle of a motion. A frame
 * is let pass first: what a click has just started only exists from the next one.
 */
export async function settle(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await new Promise(resolve => requestAnimationFrame(resolve));
    await Promise.all(document.getAnimations()
      .filter(animation => animation.effect?.getComputedTiming().endTime !== Infinity)
      .map(animation => animation.finished.catch(() => undefined)));
  });
}

/** The page itself never scrolls sideways. */
export async function expectNoSidewaysScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "the page scrolls sideways").toBeLessThanOrEqual(0);
}

/** The contrast between an element's text colour and the first opaque background behind it. */
export async function contrastOf(target: Locator): Promise<number> {
  return target.evaluate(node => {
    const parse = (value: string) => {
      const parts = value.match(/[\d.]+/g)!.map(Number);
      return { rgb: parts.slice(0, 3), alpha: parts.length > 3 ? parts[3] : 1 };
    };
    const luminance = (rgb: number[]) => {
      const [r, g, b] = rgb.map(channel => {
        const share = channel / 255;
        return share <= 0.03928 ? share / 12.92 : ((share + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    let behind = [255, 255, 255];
    for (let element: Element | null = node; element; element = element.parentElement) {
      const background = parse(getComputedStyle(element).backgroundColor);
      if (background.alpha === 1) { behind = background.rgb; break; }
    }
    const [lighter, darker] = [luminance(parse(getComputedStyle(node).color).rgb), luminance(behind)].sort((a, b) => b - a);
    return (lighter + 0.05) / (darker + 0.05);
  });
}

/** What the catalogue's toolbar is made of, as items that take room: the sections, the search, the filters, the count and the two actions. */
export const TOOLBAR_ITEMS = ":scope > nav, [data-slot=catalogue-controls] > input, [data-slot=catalogue-filters], [data-slot=catalogue-controls] [role=status], [data-slot=catalogue-actions] > button";
