import { expect, type Page } from "@playwright/test";
import { open, test } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";
const C = "macro-33333333-3333-4333-8333-333333333333";
const standalone = "run:preview-run-c1";
const expected = {
  "activity-desc": [B, A, standalone, C],
  "activity-asc": [C, standalone, A, B],
  "created-desc": [A, standalone, B, C],
  "created-asc": [C, B, standalone, A],
} as const;
type Mode = keyof typeof expected;
type Summary = { macroTaskId: string; createdAt: string | null; lastActivityAt: string | null };

function alphaGroup(page: Page) {
  return page.locator('[data-slot=macro-list] [data-slot=macro-group]')
    .filter({ has: page.locator(`[data-macro-task-id="${A}"]`) });
}

async function rows(page: Page) {
  return alphaGroup(page).locator("[data-macro-task-id]")
    .evaluateAll(nodes => nodes.map(node => node.getAttribute("data-macro-task-id")));
}

/** The time each row shows: its stamp and the screen-reader name telling which time it is. */
async function times(page: Page) {
  return alphaGroup(page).locator("[data-macro-task-id]").evaluateAll(nodes => nodes.map(node => {
    const time = node.querySelector("time");
    return [node.getAttribute("data-macro-task-id"), time?.getAttribute("datetime") ?? null,
            time?.querySelector(".sr-only")?.textContent ?? null];
  }));
}

for (const [locale, by, direction, basisLabels, directionLabels] of [
  ["zh-CN", "排序依据", "排序方向", ["最近更新", "创建时间"], ["降序", "升序"]],
  ["en-US", "Sort by", "Sort direction", ["Last updated", "Created"], ["Descending", "Ascending"]],
] as const) for (const theme of ["light", "dark"] as const) {
  test.describe(`macro sort ${locale} ${theme}`, () => {
    test.use({ locale, viewport: { width: 1280, height: 800 }, reducedMotion: "reduce" });

    test("persists both choices in the URL and renders the requested API order", async ({ page }, testInfo) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, "#tasks");
      const sort = page.locator("[data-slot=macro-sort]");
      const basis = sort.getByRole("combobox", { name: by });
      const order = sort.getByRole("combobox", { name: direction });
      await expect(basis).toHaveValue("activity");
      await expect(order).toHaveValue("desc");
      await expect(basis.locator("option")).toHaveText(basisLabels);
      await expect(order.locator("option")).toHaveText(directionLabels);
      const geometry = await sort.evaluate(node => {
        const frame = node.getBoundingClientRect();
        return [...node.querySelectorAll("select")].map(select => {
          const box = select.getBoundingClientRect();
          const style = getComputedStyle(select);
          return { left: box.left, right: box.right, top: box.top, bottom: box.bottom,
            frameLeft: frame.left, frameRight: frame.right, background: style.backgroundColor, foreground: style.color };
        });
      });
      expect(geometry).toHaveLength(2);
      for (const box of geometry) {
        expect(box.left).toBeGreaterThanOrEqual(box.frameLeft);
        expect(box.right).toBeLessThanOrEqual(box.frameRight);
        expect(box.bottom).toBeGreaterThan(box.top);
        expect(box.background).not.toBe("rgba(0, 0, 0, 0)");
        expect(box.foreground).not.toBe(box.background);
      }
      await page.screenshot({ path: testInfo.outputPath(`macro-sort-${locale}-${theme}.png`) });
      const seen: Partial<Record<Mode, Record<string, Summary>>> = {};

      async function choose(select: typeof basis, value: string, mode: Mode) {
        const read = page.waitForResponse(response => {
          const url = new URL(response.url());
          return url.pathname === "/api/macro-tasks" && url.searchParams.get("sort") === mode;
        });
        await select.selectOption(value);
        const response = await read;
        expect(response.ok()).toBe(true);
        const data = await response.json();
        expect(data.macroTasks.map((item: { macroTaskId: string }) => item.macroTaskId)).toEqual(expected[mode]);
        seen[mode] = Object.fromEntries(data.macroTasks.map((item: Summary): [string, Summary] => [item.macroTaskId, item]));
        if (mode === "activity-desc") await expect(page).not.toHaveURL(/sort=/);
        else await expect(page).toHaveURL(new RegExp(`[?&]sort=${mode}$`));
        await expectShown(mode);
      }

      /** Every row shows the time the sort is by: its stamp, and a reader-only name of which time it is. */
      async function expectShown(mode: Mode) {
        const created = mode.startsWith("created");
        const label = basisLabels[created ? 1 : 0];
        const visible = expected[mode].filter(id => id === A || id === C);
        await expect.poll(() => times(page)).toEqual(visible.map(id => {
          const summary = seen[mode]![id]!;
          return [id, created ? summary.createdAt : summary.lastActivityAt, label];
        }));
      }

      await choose(basis, "created", "created-desc");
      await choose(order, "asc", "created-asc");
      await page.reload();
      await expect(basis).toHaveValue("created");
      await expect(order).toHaveValue("asc");
      await expect.poll(() => rows(page)).toEqual([C, A]);
      await expectShown("created-asc");
      await choose(basis, "activity", "activity-asc");
      await choose(order, "desc", "activity-desc");
      await expect(page).not.toHaveURL(/sort=/);
      await page.goBack();
      await expect(basis).toHaveValue("activity");
      await expect(order).toHaveValue("asc");
      await expect.poll(() => rows(page)).toEqual([C, A]);
      await expectShown("activity-asc");
      await page.goForward();
      await expect(basis).toHaveValue("activity");
      await expect(order).toHaveValue("desc");
      await expect.poll(() => rows(page)).toEqual([A, C]);
      await expectShown("activity-desc");
    });
  });
}
