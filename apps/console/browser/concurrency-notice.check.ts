import { expect } from "@playwright/test";
import enCommon from "../src/i18n/en/common";
import zhCommon from "../src/i18n/zh/common";
import { contrastOf, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

const MODEL = "codex / openai / gpt-5-codex";
const languages = [
  { locale: "zh-CN", copy: zhCommon.concurrencyNotice, limit: "并发上限" },
  { locale: "en-US", copy: enCommon.concurrencyNotice, limit: "Concurrency limit" },
];

for (const { locale, copy, limit } of languages) for (const width of [1280, 390]) for (const theme of ["light", "dark"]) {
  test.describe(`concurrency notice in ${locale} at ${width}px in ${theme}`, () => {
    test.use({ locale, viewport: { width, height: 900 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    });

    test("keeps the full notice readable below its input and before the profile", async ({ page }) => {
      await open(page, "#buddies/catalog");
      await page.getByRole("button", { name: MODEL, exact: true }).click();
      const drawer = page.getByRole("region", { name: MODEL, exact: true });
      const notice = drawer.getByRole("note", { name: copy.title });
      const heading = notice.getByRole("heading", { name: copy.title });
      const body = notice.getByText(copy.body, { exact: true });
      const input = drawer.getByRole("spinbutton", { name: limit });
      const occupancy = drawer.locator('[id$="-occupancy"]');
      const profile = drawer.locator("[data-slot=model-profile]");
      // A valid edit already shows the model's save-before-building line. Invalidity alone must not move the controls or profile.
      await input.fill("3");
      await notice.scrollIntoViewIfNeeded();
      await expect(heading).toBeVisible();
      await expect(body).toBeVisible();
      const descriptionIds = (await input.getAttribute("aria-describedby"))!.split(" ");
      expect(descriptionIds).toContain(await notice.getAttribute("id"));
      await expect(input).toHaveAccessibleDescription(new RegExp(copy.title));
      await expectWholeAndApart(notice, notice.locator("h4, p"));
      await expectTextUncut(notice.locator("h4, p"));
      const [inputBox, occupancyBox, noticeBox, profileBox] = await Promise.all([input.boundingBox(), occupancy.boundingBox(), notice.boundingBox(), profile.boundingBox()]);
      expect(inputBox!.y + inputBox!.height).toBeLessThanOrEqual(noticeBox!.y);
      expect(noticeBox!.y + noticeBox!.height).toBeLessThanOrEqual(profileBox!.y);
      await input.fill("0");
      await expect(input).toHaveAttribute("aria-invalid", "true");
      const error = occupancy.getByRole("alert");
      await expect(error).toBeVisible();
      const [invalidInput, invalidOccupancy, invalidNotice, invalidProfile] = await Promise.all([input.boundingBox(), occupancy.boundingBox(), notice.boundingBox(), profile.boundingBox()]);
      for (const [before, after] of [[inputBox, invalidInput], [occupancyBox, invalidOccupancy], [noticeBox, invalidNotice], [profileBox, invalidProfile]]) {
        expect(Math.abs(after!.x - before!.x)).toBeLessThanOrEqual(0.5);
        expect(Math.abs(after!.y - before!.y)).toBeLessThanOrEqual(0.5);
      }
      const errorBox = await error.boundingBox();
      expect(errorBox!.y).toBeGreaterThanOrEqual(invalidOccupancy!.y);
      expect(errorBox!.y + errorBox!.height).toBeLessThanOrEqual(invalidOccupancy!.y + invalidOccupancy!.height);
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
