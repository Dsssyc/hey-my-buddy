import { expect } from "@playwright/test";
import enCommon from "../src/i18n/en/common";
import zhCommon from "../src/i18n/zh/common";
import { contrastOf, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

const MODEL = "codex / openai / gpt-5-codex";
const languages = [
  { locale: "zh-CN", copy: zhCommon.concurrencyNotice, limit: "并发上限" },
  { locale: "en-US", copy: enCommon.concurrencyNotice, limit: "Concurrency limit" },
];

for (const { locale, copy, limit } of languages) for (const width of [1440]) for (const theme of ["light", "dark"]) {
  test.describe(`concurrency notice in ${locale} at ${width}px in ${theme}`, () => {
    test.use({ locale, viewport: { width, height: 900 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    });

    test("keeps the full notice readable below its input", async ({ page }) => {
      await open(page, "#buddies/catalog");
      await page.getByRole("button", { name: MODEL, exact: true }).click();
      const drawer = page.getByRole("region", { name: MODEL, exact: true });
      const notice = drawer.getByRole("note", { name: copy.title });
      const heading = notice.getByRole("heading", { name: copy.title });
      const body = notice.getByText(copy.body, { exact: true });
      const input = drawer.getByRole("spinbutton", { name: limit });
      const occupancy = drawer.locator('[id$="-occupancy"]');
      await expect(drawer.locator("[data-slot=model-profile], [data-slot=kind-evaluation]")).toHaveCount(0);
      await expect(notice).toHaveCount(0);
      // The notice belongs to the service controls and does not move the inputs.
      await input.fill("3");
      await notice.scrollIntoViewIfNeeded();
      await expect(heading).toBeVisible();
      await expect(body).toBeVisible();
      const descriptionIds = (await input.getAttribute("aria-describedby"))!.split(" ");
      expect(descriptionIds).toContain(await notice.getAttribute("id"));
      await expect(input).toHaveAccessibleDescription(new RegExp(copy.title));
      await expectWholeAndApart(notice, notice.locator("h4, p"));
      await expectTextUncut(notice.locator("h4, p"));
      const [inputBox, occupancyBox, noticeBox] = await Promise.all([input.boundingBox(), occupancy.boundingBox(), notice.boundingBox()]);
      expect(inputBox!.y + inputBox!.height).toBeLessThanOrEqual(noticeBox!.y);
      expect(occupancyBox!.y).toBeGreaterThanOrEqual(inputBox!.y);
      for (const text of [heading, body]) {
        expect(await contrastOf(text)).toBeGreaterThanOrEqual(4.5);
        await expectTextUncut(text);
      }
      await input.fill("2");
      await expect(notice).toHaveCount(0);
      await input.fill("1");
      await expect(notice).toHaveCount(0);
      await input.fill("0");
      await expect(input).toHaveAttribute("aria-invalid", "true");
      await expect(drawer.getByRole("alert")).toBeVisible();
      await input.fill("3");
      await expect(notice).toBeVisible();
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
