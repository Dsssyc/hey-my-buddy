import { expect } from "@playwright/test";
import { contrastOf, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

// The models table of the Buddy page with its side column, on the synthetic
// preview's catalogue: three harnesses that work and one that is not logged in.
const languages = [
  { locale: "zh-CN", unsaved: "未保存的修改", headers: ["Buddy", "Harness", "提供方", "模型", "推理强度", "并发"] },
  { locale: "en-US", unsaved: "Unsaved changes", headers: ["Buddy", "Harness", "Provider", "Model", "Reasoning effort", "Concurrency"] },
];
const FLASH = "dsh / deepseek-official / deepseek-flash";

for (const { locale, unsaved, headers } of languages) {
  test.describe(`the models table in ${locale} at 1440px`, () => {
    test.use({ locale, viewport: { width: 1440, height: 900 } });

    test("sets the table and its side column side by side, each part whole", async ({ page }) => {
      await open(page, "#buddies");
      const table = page.getByRole("table");
      const pane = page.getByRole("complementary");
      await expect(table.getByRole("columnheader")).toHaveText(headers);
      const [tableBox, paneBox, main] = [await table.boundingBox(), await pane.boundingBox(), await page.getByRole("main").boundingBox()];
      // The side column is 352px with the line that starts it; the table runs from the window's edge to that line.
      expect(paneBox!.width).toBe(351);
      expect(paneBox!.x + paneBox!.width).toBe(main!.width);
      expect(tableBox!.x).toBe(0);
      expect(Math.round(tableBox!.x + tableBox!.width)).toBe(paneBox!.x - 1);
      await expectWholeAndApart(page.getByRole("main"), page.locator("[data-slot=models-toolbar] > *"));
      for (const row of await table.locator("tbody tr").all()) {
        await expectWholeAndApart(table, row.getByRole("cell"));
        const chips = row.locator("[data-slot=buddy-chip]");
        if (await chips.count()) await expectWholeAndApart(row.getByRole("cell").nth(3), chips);
      }
      await expectTextUncut(table.getByRole("button"));
      await expectTextUncut(table.locator("[data-slot=buddy-chip]"));
      await expectNoSidewaysScroll(page);
    });

    test("keeps every setting of the side column inside it", async ({ page }) => {
      await open(page, "#buddies");
      await page.getByRole("table").getByRole("button", { name: FLASH }).click();
      const pane = page.getByRole("complementary", { name: FLASH });
      await expectWholeAndApart(pane, pane.getByRole("group"));
      for (const level of await pane.getByRole("group").all())
        await expectWholeAndApart(level, level.locator(":scope > *"));
      await expectWholeAndApart(pane, pane.getByRole("radio"));
      await expectWholeAndApart(pane, pane.locator("input, textarea, [role=radiogroup]"));
      await expectTextUncut(pane.getByRole("radio"));
      await expectTextUncut(pane.getByRole("combobox"));
      // What is chosen and what is on look different from the rest, also where a hint wraps the control.
      const fill = (role: "radio" | "switch", checked: boolean) => pane.getByRole(role, { checked }).first()
        .evaluate(node => getComputedStyle(node).backgroundColor);
      expect(await fill("radio", true)).not.toBe(await fill("radio", false));
      const on = await fill("switch", true);
      await pane.getByRole("switch", { checked: true }).first().click();
      // The colour moves over a moment, so the look is read once it has settled.
      await expect.poll(() => fill("switch", false)).not.toBe(on);
      expect(await fill("switch", true)).toBe(on);
      for (const radio of await pane.getByRole("radio").all()) expect(await contrastOf(radio)).toBeGreaterThanOrEqual(4.5);
    });

    test("draws the selected row, a folded harness and an unavailable one so that each can be read", async ({ page }) => {
      await open(page, "#buddies");
      const table = page.getByRole("table");
      await page.getByRole("checkbox").nth(1).click();
      // Three harnesses work; the fourth appears once the unavailable buddies are listed.
      await expect(table.locator("tbody")).toHaveCount(4);
      const selected = table.getByRole("button", { pressed: true });
      expect(await contrastOf(selected)).toBeGreaterThanOrEqual(4.5);
      // The selected row is tinted; the row after it is not.
      const tint = (row: number) => table.locator("tbody tr").nth(row).evaluate(node => getComputedStyle(node).backgroundColor);
      await expect(table.locator("tbody tr").first().getByRole("button", { pressed: true })).toHaveCount(1);
      expect(await tint(0)).not.toBe(await tint(1));
      // The harness that is not logged in comes last, folded into one row.
      const folded = table.locator("tbody").last();
      await expect(folded.getByRole("row")).toHaveCount(1);
      for (const cell of await folded.getByRole("cell").filter({ hasText: /\S/ }).all())
        expect(await contrastOf(cell)).toBeGreaterThanOrEqual(4.5);
      await folded.getByRole("button", { expanded: false }).click();
      await expect(folded.getByRole("row")).toHaveCount(3);
      for (const chip of await folded.locator("[data-slot=buddy-chip]").all())
        expect(await contrastOf(chip)).toBeGreaterThanOrEqual(4.5);
      for (const row of await folded.getByRole("row").all()) await expectWholeAndApart(table, row.getByRole("cell"));
      await expectNoSidewaysScroll(page);
    });

    test("shows the save bar across the bottom once something is changed, and marks what changed", async ({ page }) => {
      await open(page, "#buddies");
      const pane = page.getByRole("complementary");
      await expect(page.getByRole("region", { name: unsaved })).toHaveCount(0);
      await pane.getByRole("switch").first().click();
      const bar = page.getByRole("region", { name: unsaved });
      const [barBox, viewport] = [await bar.boundingBox(), page.viewportSize()!];
      expect(barBox!.x).toBe(0);
      expect(barBox!.width).toBe(viewport.width);
      expect(barBox!.y + barBox!.height).toBe(viewport.height);
      await expectWholeAndApart(bar, bar.locator(":scope > *"));
      expect(await contrastOf(bar.getByRole("status"))).toBeGreaterThanOrEqual(4.5);
      // The same mark on the buddy's row in the side column and on its chip in the table.
      await expect(pane.getByRole("img")).toHaveCount(1);
      await expect(page.getByRole("table").getByRole("img")).toHaveCount(1);
      await expectWholeAndApart(pane, pane.getByRole("group"));
    });
  });

  test.describe(`the models table in ${locale} at 1100px`, () => {
    test.use({ locale, viewport: { width: 1100, height: 800 } });

    test("gives the table the whole width where the window has no room for both", async ({ page }) => {
      await open(page, "#buddies");
      const table = page.getByRole("table");
      const pane = page.getByRole("complementary");
      await expect(pane).toBeHidden();
      expect((await table.boundingBox())!.width).toBe(1100);
      await expectWholeAndApart(page.getByRole("main"), page.locator("[data-slot=models-toolbar] > *"));
      for (const row of await table.locator("tbody tr").all()) await expectWholeAndApart(table, row.getByRole("cell"));
      await expectNoSidewaysScroll(page);
      await table.getByRole("button", { name: FLASH }).click();
      await expect(table).toBeHidden();
      expect((await pane.boundingBox())!.width).toBe(1100);
    });
  });

  test.describe(`the models table in ${locale} at 390px`, () => {
    test.use({ locale, viewport: { width: 390, height: 844 } });

    test("scrolls the table inside its own area and gives a row's settings the whole width", async ({ page }) => {
      await open(page, "#buddies");
      const table = page.getByRole("table");
      const pane = page.getByRole("complementary");
      await expect(pane).toBeHidden();
      await expectWholeAndApart(page.getByRole("main"), page.locator("[data-slot=models-toolbar] > *"));
      await expectNoSidewaysScroll(page);
      // The table is wider than the window; the page stays put and the table scrolls.
      expect((await table.boundingBox())!.width).toBeGreaterThan(390);
      await table.getByRole("button", { name: FLASH }).click();
      await expect(table).toBeHidden();
      const paneBox = await pane.boundingBox();
      expect([paneBox!.x, paneBox!.width]).toEqual([0, 390]);
      await expectWholeAndApart(pane, pane.getByRole("group"));
      await expectWholeAndApart(pane, pane.locator("input, textarea, [role=radiogroup]"));
      await expectNoSidewaysScroll(page);
      // The way back to the table is the first thing in the side column.
      await pane.getByRole("button").first().click();
      await expect(pane).toBeHidden();
      await expect(table).toBeVisible();
    });
  });
}
