import { expect, type Page } from "@playwright/test";
import { expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, settle, test } from "./preview";

// All three are real synthetic preview records in the same macro task. The ZCode
// turn is still running; the two terminal questions belong to the ended DSH turn.
const MACRO = "macro-22222222-2222-4222-8222-222222222222";
const labels = [
  { locale: "zh-CN", turn: "回合", queued: "在 Worker 下次检查点时送达，现在尚未送达。",
    unanswered: "问询已送达，但 Worker 在回合结束前没有答复。", ended: "此回合已结束，问询未排入。" },
  { locale: "en-US", turn: "Turn", queued: "Will be delivered at the Worker's next checkpoint; not delivered yet.",
    unanswered: "The inquiry was delivered, but the Worker did not answer before the turn ended.",
    ended: "The turn had ended before the inquiry could be queued." },
];

/** Check the actual rendered line fragments, including wrapping and scroll clipping. */
async function expectInquiryWhole(page: Page, inquiryId: string, text: string) {
  const inquiry = page.locator(`[data-slot=detail-pane] [data-inquiry-id="${inquiryId}"]`);
  await expect(inquiry).toBeVisible();
  await inquiry.scrollIntoViewIfNeeded();
  const label = inquiry.getByText(text, { exact: true });
  await expect(label).toBeVisible();
  await expectTextUncut(inquiry.locator("span:visible"));
  const header = inquiry.locator(".bg-sunk > div").first();
  await expectWholeAndApart(header, header.locator(":scope > span"));
  const timeLines = await header.locator("time").evaluate(element => {
    const range = document.createRange();
    range.selectNodeContents(element);
    return [...new Set([...range.getClientRects()].filter(rect => rect.width > 0).map(rect => rect.top))];
  });
  expect(timeLines, "the inquiry date and time stay on one line").toHaveLength(1);
  if (page.viewportSize()!.width === 390) {
    const head = (await header.boundingBox())!;
    const time = (await header.locator("time").boundingBox())!;
    const state = (await header.locator(":scope > span").last().boundingBox())!;
    const stateLines = await label.evaluate(element => {
      const range = document.createRange();
      range.selectNodeContents(element);
      return [...range.getClientRects()].filter(rect => rect.width > 0 && rect.height > 0)
        .map(rect => ({ top: rect.top, bottom: rect.bottom }));
    });
    const inline = stateLines.length === 1 && stateLines[0]!.top < time.y + time.height && stateLines[0]!.bottom > time.y;
    expect(inline || Math.abs(state.x - head.x) < 0.1,
      "an inquiry state fits on the time's line or starts at the full header's left edge").toBe(true);
    expect(inline || state.y >= time.y + time.height,
      "an inquiry state beside the time stays on one line; a wrapping state starts below it").toBe(true);
  }
  const clipped = await label.evaluate(element => {
    const quote = element.closest(".bg-sunk")!.getBoundingClientRect();
    const viewport = element.closest("[data-slot=scroll-area-viewport]")!.getBoundingClientRect();
    const range = document.createRange();
    range.selectNodeContents(element);
    return [...range.getClientRects()].filter(rect => rect.width > 0 && rect.height > 0).some(rect =>
      rect.left < Math.max(quote.left, viewport.left, 0) - 1 ||
      rect.right > Math.min(quote.right, viewport.right, window.innerWidth) + 1 ||
      rect.top < Math.max(viewport.top, 0) - 1 || rect.bottom > Math.min(viewport.bottom, window.innerHeight) + 1);
  });
  expect(clipped, `the full inquiry state is visible: ${text}`).toBe(false);
  const overflow = await page.locator("[data-slot=detail-pane] [data-slot=scroll-area-viewport]")
    .evaluate(element => element.scrollWidth - element.clientWidth);
  expect(overflow, "the inquiry pane has no horizontal overflow").toBeLessThanOrEqual(0);
  await expectNoSidewaysScroll(page);
}

for (const words of labels) for (const width of [390, 1440]) for (const theme of ["dark", "light"] as const) {
  test.describe(`inquiry states in the synthetic preview ${words.locale} ${width}px ${theme}`, () => {
    test.use({ locale: words.locale, viewport: { width, height: width === 390 ? 844 : 900 },
      colorScheme: theme, reducedMotion: "reduce" });

    test("shows checkpoint queuing and both ended states in full without overflow", async ({ page }, testInfo) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/${MACRO}/preview-run-b8`);
      await page.locator("[data-slot=detail-strip]").getByRole("button", { name: new RegExp(`^${words.turn} 1(?: ·|$)`) }).press("Enter");
      await expect(page.locator("[data-slot=detail-pane]")).toContainText("zcode / zai / glm-5");
      await expectInquiryWhole(page, "inq-b8-checkpoint", words.queued);
      await settle(page);
      await page.screenshot({ path: testInfo.outputPath(`inquiry-queued-${words.locale}-${width}.png`) });

      await page.evaluate(hash => { window.location.hash = hash; }, `#tasks/${MACRO}/preview-run-b9`);
      await page.locator("[data-slot=detail-strip]").getByRole("button", { name: new RegExp(`^${words.turn} 2(?: ·|$)`) }).press("Enter");
      await expect(page.locator("[data-slot=detail-pane]")).toContainText("dsh / deepseek-official / deepseek-flash");
      for (const [id, text, name] of [
        ["inq-b9-unanswered", words.unanswered, "unanswered"],
        ["inq-b9-after-end", words.ended, "after-end"],
      ] as const) {
        await test.step(`${name} text geometry`, () => expectInquiryWhole(page, id, text));
        await test.step(`${name} settled screenshot`, async () => {
          await settle(page);
          await page.screenshot({ path: testInfo.outputPath(`inquiry-${name}-${words.locale}-${width}.png`) });
        });
      }
      if (width === 390) {
        const starts = await page.locator("[data-slot=detail-pane] [data-inquiry-id] .bg-sunk > div > span:last-child")
          .evaluateAll(nodes => nodes.map(node => {
            const state = node.getBoundingClientRect();
            const time = node.parentElement!.querySelector("time")!.getBoundingClientRect();
            return { left: state.left, ownLine: state.top >= time.bottom };
          }));
        expect(starts).toHaveLength(2);
        if (starts.every(state => state.ownLine)) {
          expect(starts[0]!.left, "adjacent inquiry states on their own lines start at the same position").toBeCloseTo(starts[1]!.left, 1);
        }
      }
    });
  });
}
