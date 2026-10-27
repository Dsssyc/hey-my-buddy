import { expect } from "@playwright/test";
import { contrastOf, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

// The roles section of the Buddy page on the synthetic preview: a Router list of
// three, one serving, one waiting to be tried again and one that cannot serve.
const languages = [
  { locale: "zh-CN", view: "角色", list: "Router 列表", sections: ["列表", "默认模式", "审阅预算", "重试间隔", "记录"], add: "添加", addTitle: "添加 Router",
    routers: "各 Router 的记录", failures: "最近的失败", standing: ["当前", "等待重试", "不可用"] },
  { locale: "en-US", view: "Roles", list: "Router list", sections: ["List", "Default mode", "Review budget", "Retry interval", "Records"], add: "Add", addTitle: "Add a Router",
    routers: "Records of each Router", failures: "Recent failures", standing: ["Current", "Waiting to retry", "Unavailable"] },
];

for (const { locale, view, list, sections, add, addTitle, routers, failures, standing } of languages) {
  test.describe(`the roles section in ${locale} at 1440px`, () => {
    test.use({ locale, viewport: { width: 1440, height: 900 } });

    test("sets every section's name beside its content, each part whole", async ({ page }) => {
      await open(page, "#buddies/roles");
      const region = page.getByRole("region", { name: view });
      for (const name of sections) {
        const section = region.getByRole("group", { name, exact: true });
        await expect(section).toBeVisible();
        await expectWholeAndApart(section, section.locator(":scope > *"));
      }
      const lines = region.getByRole("list", { name: list }).getByRole("listitem");
      await expect(lines).toHaveCount(3);
      for (const [index, line] of (await lines.all()).entries()) {
        await expect(line).toContainText(standing[index]);
        await expectWholeAndApart(line, line.locator(":scope > *"));
      }
      await expectTextUncut(region.getByRole("button"));
      await expectTextUncut(region.getByRole("radio"));
      await expectNoSidewaysScroll(page);
    });

    test("shows every column of the records without scrolling them sideways", async ({ page }) => {
      await open(page, "#buddies/roles");
      for (const name of [routers, failures]) {
        const table = page.getByRole("table", { name });
        await table.scrollIntoViewIfNeeded();
        const room = await table.evaluate(node => {
          const viewport = node.closest("[data-slot=scroll-area-viewport]")!;
          return viewport.scrollWidth - viewport.clientWidth;
        });
        expect(room, `${name} scrolls sideways`).toBeLessThanOrEqual(0);
        for (const row of await table.getByRole("row").all()) await expectWholeAndApart(table, row.locator("th, td"));
      }
    });

    test("wraps the long hint of a mode, reached by the keyboard, inside 320px", async ({ page }) => {
      await open(page, "#buddies/roles");
      for (let step = 0; step < 60 && !(await page.evaluate(() => document.activeElement?.getAttribute("role") === "radio")); step++)
        await page.keyboard.press("Tab");
      // Hints passed on the way are still closing: the one that is open is the one read.
      const hint = page.locator("[data-slot=tooltip-content][data-state$=open]");
      await expect(hint).toBeVisible();
      const box = (await hint.boundingBox())!;
      expect(box.width).toBeLessThanOrEqual(320);
      // More than one line of 12px text: the sentence was wrapped, not cut.
      expect(box.height).toBeGreaterThan(40);
      await expectWholeAndApart(page.locator("body"), hint);
    });

    test("opens the add dialog by a click without a hint over it", async ({ page }) => {
      await open(page, "#buddies/roles");
      await page.getByRole("region", { name: view }).getByRole("button", { name: add, exact: true }).click();
      const dialog = page.getByRole("dialog", { name: addTitle });
      await expect(dialog).toBeVisible();
      await expect(page.getByRole("tooltip")).toHaveCount(0);
      expect(await dialog.evaluate(node => getComputedStyle(node).borderTopLeftRadius)).toBe("0px");
      await expectWholeAndApart(page.locator("body"), dialog);
    });
  });

  test.describe(`the roles section in ${locale} at 390px`, () => {
    test.use({ locale, viewport: { width: 390, height: 844 } });

    test("breaks a buddy's four parts only between them and never scrolls the page sideways", async ({ page }) => {
      await open(page, "#buddies/roles");
      const names = page.getByRole("region", { name: view }).getByRole("list", { name: list }).getByRole("link");
      await expect(names).toHaveCount(3);
      // A part is whole when all of its letters stand on one line.
      const broken = await names.evaluateAll(links => links.flatMap(link => {
        const text = link.firstChild!;
        let offset = 0;
        return (text.textContent ?? "").split(" / ").filter(part => {
          const range = document.createRange();
          range.setStart(text, offset);
          range.setEnd(text, offset + part.length);
          offset += part.length + 3;
          return new Set([...range.getClientRects()].map(rect => Math.round(rect.top))).size > 1;
        });
      }));
      expect(broken, "parts broken inside").toEqual([]);
      await expectNoSidewaysScroll(page);
    });
  });
}

test.describe("the roles section in the dark theme", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 900 } });

  test("keeps every text readable against what is behind it", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
    await open(page, "#buddies/roles");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    const region = page.getByRole("region", { name: "角色" });
    for (const text of await region.locator("span, code, a, h2, button, time, th, td, caption, p").all()) {
      if (!(await text.innerText()).trim() || await text.locator("*").count()) continue;
      expect(await contrastOf(text), await text.innerText()).toBeGreaterThanOrEqual(4.5);
    }
  });
});
