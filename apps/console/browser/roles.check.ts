import { expect } from "@playwright/test";
import { contrastOf, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

// The roles section of the Buddy page on the synthetic preview: a Router list of
// three, one serving, one waiting to be tried again and one that cannot serve.
const languages = [
  { locale: "zh-CN", view: "Router", list: "Router 列表", sections: ["列表", "默认模式", "等待模型开始作答（秒）", "重试间隔", "画像 buddy", "记录"], profile: "画像 buddy", wait: "等待模型开始作答（秒）", chooseProfile: "选择 buddy", add: "添加", addTitle: "添加 Router",
    routers: "各 Router 的记录", failures: "最近的失败", standing: ["当前", "等待重试", "不可用"] },
  { locale: "en-US", view: "Router", list: "Router list", sections: ["List", "Default mode", "Wait for the model to start responding (seconds)", "Retry interval", "Profile buddy", "Records"], profile: "Profile buddy", wait: "Wait for the model to start responding (seconds)", chooseProfile: "Choose buddy", add: "Add", addTitle: "Add a Router",
    routers: "Records of each Router", failures: "Recent failures", standing: ["Current", "Waiting to retry", "Unavailable"] },
];

for (const { locale, view, list, sections, profile, wait, chooseProfile, add, addTitle, routers, failures, standing } of languages) {
  test.describe(`the roles section in ${locale} at 1440px`, () => {
    test.use({ locale, viewport: { width: 1440, height: 900 } });

    test("sets every section's name beside its content, each part whole", async ({ page }) => {
      await open(page, "#buddies/router");
      const region = page.getByRole("region", { name: view });
      for (const name of sections) {
        const section = region.getByRole("group", { name, exact: true });
        await expect(section).toBeVisible();
        await expectWholeAndApart(section, section.locator(":scope > *"));
      }
      await expect(region.getByRole("spinbutton", { name: wait })).toHaveValue("60");
      await expect(region.getByRole("group", { name: locale === "zh-CN" ? "画像预算" : "Profile budget" })).toHaveCount(0);
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
      await open(page, "#buddies/router");
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

    test("writes the chosen mode's consequence below the choice and leaves its hover quiet", async ({ page }) => {
      await open(page, "#buddies/router");
      const consequence = page.locator('[data-slot="routing-mode-consequence"]');
      const fast = page.getByRole("radio", { name: locale === "zh-CN" ? "快速" : "Fast", exact: true });
      const review = page.getByRole("radio", { name: locale === "zh-CN" ? "审阅" : "Review", exact: true });
      await fast.click();
      await expect(consequence).not.toContainText(locale === "zh-CN" ? "代码副本" : "copy of the code");
      await review.click();
      await expect(consequence).toContainText(locale === "zh-CN" ? "代码副本" : "copy of the code");
      expect((await consequence.boundingBox())!.y).toBeGreaterThan((await review.boundingBox())!.y);
      await review.hover();
      await page.waitForTimeout(700);
      await expect(page.getByRole("tooltip")).toHaveCount(0);
    });

    test("opens the add dialog by a click without a hint over it", async ({ page }) => {
      await open(page, "#buddies/router");
      await page.getByRole("region", { name: view }).getByRole("button", { name: add, exact: true }).click();
      const dialog = page.getByRole("dialog", { name: addTitle });
      await expect(dialog).toBeVisible();
      await expect(page.getByRole("tooltip")).toHaveCount(0);
      expect(await dialog.evaluate(node => getComputedStyle(node).borderTopLeftRadius)).toBe("6px");
      await expectWholeAndApart(page.locator("body"), dialog);
    });
  });

  test.describe(`the roles section in ${locale} at 390px`, () => {
    test.use({ locale, viewport: { width: 390, height: 844 } });

    test("breaks a buddy's four parts only between them and never scrolls the page sideways", async ({ page }) => {
      await open(page, "#buddies/router");
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

    test("keeps profile settings and the buddy chooser within the narrow viewport", async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 900 });
      await open(page, "#buddies/router");
      const region = page.getByRole("region", { name: view });
      const buddy = region.getByRole("group", { name: profile, exact: true });
      await buddy.scrollIntoViewIfNeeded();
      await expectTextUncut(buddy.locator("p, button, span"));
      const responseWait = region.getByRole("group", { name: wait, exact: true });
      await expectWholeAndApart(responseWait, responseWait.locator(":scope > *"));
      await expectNoSidewaysScroll(page);
      await buddy.getByRole("button", { name: chooseProfile }).click();
      const dialog = page.getByRole("dialog", { name: chooseProfile });
      await expect(dialog).toBeVisible();
      await expectWholeAndApart(page.locator("body"), dialog);
      await expectNoSidewaysScroll(page);
    });
  });
}

test.describe("the roles section in the dark theme", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 900 } });

  test("keeps every text readable against what is behind it", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
    await open(page, "#buddies/router");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    const region = page.getByRole("region", { name: "Router" });
    for (const text of await region.locator("span, code, a, h2, button, time, th, td, caption, p").all()) {
      if (!(await text.innerText()).trim() || await text.locator("*").count()) continue;
      expect(await contrastOf(text), await text.innerText()).toBeGreaterThanOrEqual(4.5);
    }
  });
});
