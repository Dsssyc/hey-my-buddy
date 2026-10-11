import { expect, type Page } from "@playwright/test";
import { contrastOf, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

// The harness table of the Buddy page with its side column, on the synthetic
// preview: three harnesses that are found and one that is not logged in.
const languages = [
  { locale: "zh-CN", table: "Harness", headers: ["Harness", "状态", "版本", "账户", "额度"], check: "检测", checked: "已检测 Codex：已找到", manage: "管理账户", back: "Harness" },
  { locale: "en-US", table: "Harnesses", headers: ["Harness", "Status", "Version", "Account", "Quota"], check: "Check", checked: "Checked Codex: Found", manage: "Manage account", back: "Harnesses" },
];
const harnessTable = (page: Page, name: string) => page.getByRole("table", { name });

for (const { locale, table: tableName, headers, check, checked, manage, back } of languages) {
  test.describe(`the harness table in ${locale} at 1440px`, () => {
    test.use({ locale, viewport: { width: 1440, height: 900 } });

    test("sets the table and its side column side by side, each part whole", async ({ page }) => {
      await open(page, "#buddies/harness");
      const table = harnessTable(page, tableName);
      const pane = page.getByRole("complementary");
      await expect(table.getByRole("columnheader")).toHaveText(headers);
      const [tableBox, paneBox, main] = [await table.boundingBox(), await pane.boundingBox(), await page.getByRole("main").boundingBox()];
      expect(paneBox!.width).toBe(351);
      expect(paneBox!.x + paneBox!.width).toBe(main!.width);
      expect(tableBox!.x).toBe(0);
      expect(Math.round(tableBox!.x + tableBox!.width)).toBe(paneBox!.x - 1);
      await expectWholeAndApart(page.getByRole("main"), page.locator("[data-slot=harness-toolbar] > *"));
      for (const row of await table.locator("tbody tr").all()) await expectWholeAndApart(table, row.getByRole("cell"));
      await expectWholeAndApart(pane, pane.getByRole("group"));
      for (const block of await pane.getByRole("group").all()) await expectWholeAndApart(block, block.locator(":scope > *"));
      await expectTextUncut(table.getByRole("button"));
      await expectTextUncut(pane.getByRole("button"));
      await expectNoSidewaysScroll(page);
    });

    test("says how a check ended under the toolbar, beside the side column", async ({ page }) => {
      await open(page, "#buddies/harness");
      await harnessTable(page, tableName).getByRole("button", { name: "Codex" }).click();
      const pane = page.getByRole("complementary", { name: "Codex" });
      await pane.getByRole("button", { name: check, exact: true }).click();
      const line = page.locator("[data-slot=harness-feedback]");
      await expect(line).toHaveText(checked);
      const [toolbar, said, column] = [await page.locator("[data-slot=harness-toolbar]").boundingBox(), await line.boundingBox(), await pane.boundingBox()];
      expect(Math.round(said!.y)).toBe(Math.round(toolbar!.y + toolbar!.height));
      expect(said!.x + said!.width).toBeLessThanOrEqual(column!.x);
    });

    test("opens the account dialog by a click without a hint over it, square and on the card colour", async ({ page }) => {
      await open(page, "#buddies/harness");
      await harnessTable(page, tableName).getByRole("button", { name: "Codex" }).click();
      const card = await page.getByRole("complementary", { name: "Codex" }).evaluate(node => getComputedStyle(node.closest("section")!).backgroundColor);
      await page.getByRole("complementary", { name: "Codex" }).getByRole("button", { name: manage }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      // Focus moved into the dialog by the page shows no hint; one would lie over the dialog's title.
      await expect(dialog.locator(":focus")).toHaveCount(1);
      await expect(page.getByRole("tooltip")).toHaveCount(0);
      expect(await dialog.evaluate(node => [getComputedStyle(node).borderTopLeftRadius, getComputedStyle(node).backgroundColor])).toEqual(["0px", card]);
      // Every button of the dialog is its own and in the page's language: none of a library sits in its corner.
      if (locale === "zh-CN") await expect(dialog.getByRole("button", { name: "Close" })).toHaveCount(0);
      await expectWholeAndApart(page.locator("body"), dialog);
      await page.keyboard.press("Escape");
      await expect(dialog).toHaveCount(0);
    });

    test("shows a hint on keyboard focus, wrapped inside the window", async ({ page }) => {
      await open(page, "#buddies/harness");
      await page.keyboard.press("Tab");
      for (let step = 0; step < 40 && !(await page.locator("[data-slot=tooltip-content][data-state$=open]").count()); step++) await page.keyboard.press("Tab");
      // Hints passed on the way are still closing: the one that is open is the one read.
      const hint = page.locator("[data-slot=tooltip-content][data-state$=open]");
      await expect(hint).toBeVisible();
      expect((await hint.boundingBox())!.width).toBeLessThanOrEqual(320);
      await expectWholeAndApart(page.locator("body"), hint);
    });
  });

  test.describe(`the harness table in ${locale} at 1100px`, () => {
    test.use({ locale, viewport: { width: 1100, height: 800 } });

    test("lets an opened row's side column take the table's place and keeps what a check said in view", async ({ page }) => {
      await open(page, "#buddies/harness");
      const table = harnessTable(page, tableName);
      await expect(page.getByRole("complementary")).toBeHidden();
      await table.getByRole("button", { name: "Codex" }).click();
      const pane = page.getByRole("complementary", { name: "Codex" });
      await expect(pane).toBeVisible();
      await expect(table).toBeHidden();
      await expect(page.locator("[data-slot=harness-toolbar]")).toBeHidden();
      await pane.getByRole("button", { name: check, exact: true }).click();
      const line = page.locator("[data-slot=harness-feedback]");
      await expect(line).toBeVisible();
      await expect(line).toHaveText(checked);
      // The line lies above the side column, inside the window.
      const [said, column] = [await line.boundingBox(), await pane.boundingBox()];
      expect(said!.y + said!.height).toBeLessThanOrEqual(column!.y + 0.5);
      await expectWholeAndApart(page.getByRole("main"), line);
      await pane.getByRole("button", { name: back, exact: true }).click();
      await expect(table).toBeVisible();
      await expect(line).toBeVisible();
      await expectNoSidewaysScroll(page);
    });
  });

  test.describe(`the harness table in ${locale} at 390px`, () => {
    test.use({ locale, viewport: { width: 390, height: 844 } });

    test("keeps the page from scrolling sideways with the table or the side column open", async ({ page }) => {
      await open(page, "#buddies/harness");
      await expectNoSidewaysScroll(page);
      await harnessTable(page, tableName).getByRole("button", { name: "DSH" }).click();
      const pane = page.getByRole("complementary", { name: "DSH" });
      await expectWholeAndApart(pane, pane.getByRole("group"));
      await expectTextUncut(pane.getByRole("button"));
      await expectNoSidewaysScroll(page);
    });
  });
}

test.describe("the harness table in the dark theme", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 900 } });

  test("keeps every text readable against what is behind it", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
    await open(page, "#buddies/harness");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    const table = harnessTable(page, "Harness");
    const pane = page.getByRole("complementary");
    for (const text of [...await table.getByRole("cell").all(), ...await table.getByRole("columnheader").all(),
      ...await pane.locator("span, code, h2, h3, button, time").all()]) {
      if (!(await text.innerText()).trim() || await text.locator("*").count()) continue;
      expect(await contrastOf(text), await text.innerText()).toBeGreaterThanOrEqual(4.5);
    }
  });
});
