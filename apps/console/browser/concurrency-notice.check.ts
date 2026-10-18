import { expect } from "@playwright/test";
import enCommon from "../src/i18n/en/common";
import zhCommon from "../src/i18n/zh/common";
import { contrastOf, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

const MODEL = "codex / openai / gpt-5-codex";
const languages = [
  { locale: "zh-CN", copy: zhCommon.concurrencyNotice, limit: "并发上限", note: "备注" },
  { locale: "en-US", copy: enCommon.concurrencyNotice, limit: "Concurrency limit", note: "Note" },
];

for (const { locale, copy, limit, note } of languages) for (const width of [1280, 390]) for (const theme of ["light", "dark"]) {
  test.describe(`concurrency notice in ${locale} at ${width}px in ${theme}`, () => {
    test.use({ locale, viewport: { width, height: 900 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    });

    test("keeps the full notice readable below its input and before the note", async ({ page }) => {
      await open(page, "#buddies/catalog");
      await page.getByRole("button", { name: MODEL, exact: true }).click();
      const drawer = page.getByRole("region", { name: MODEL, exact: true });
      const notice = drawer.getByRole("note", { name: copy.title });
      const heading = notice.getByRole("heading", { name: copy.title });
      const body = notice.getByText(copy.body, { exact: true });
      const input = drawer.getByRole("spinbutton", { name: limit });
      const noteInput = drawer.getByRole("textbox", { name: note });
      await notice.scrollIntoViewIfNeeded();
      await expect(heading).toBeVisible();
      await expect(body).toBeVisible();
      const descriptionIds = (await input.getAttribute("aria-describedby"))!.split(" ");
      expect(descriptionIds).toContain(await notice.getAttribute("id"));
      await expect(input).toHaveAccessibleDescription(new RegExp(copy.title));
      await expectWholeAndApart(notice, notice.locator("h4, p"));
      await expectTextUncut(notice.locator("h4, p"));
      const [inputBox, noticeBox, noteBox] = await Promise.all([input.boundingBox(), notice.boundingBox(), noteInput.boundingBox()]);
      expect(inputBox!.y + inputBox!.height).toBeLessThanOrEqual(noticeBox!.y);
      expect(noticeBox!.y + noticeBox!.height).toBeLessThanOrEqual(noteBox!.y);
      for (const text of [heading, body]) {
        expect(await contrastOf(text)).toBeGreaterThanOrEqual(4.5);
        const style = await text.evaluate(node => {
          const computed = getComputedStyle(node);
          return { fontSize: parseFloat(computed.fontSize), lineHeight: parseFloat(computed.lineHeight) };
        });
        expect(style.fontSize).toBeGreaterThanOrEqual(14);
        expect(style.lineHeight / style.fontSize).toBeGreaterThanOrEqual(1.5);
      }
      await expectNoSidewaysScroll(page);
    });
  });
}
