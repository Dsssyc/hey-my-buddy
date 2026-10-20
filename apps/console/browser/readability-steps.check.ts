import { expect } from "@playwright/test";
import { test } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";

for (const { locale, previous, next, copy, failed } of [
  { locale: "zh-CN", previous: "上一步", next: "下一步", copy: "复制标识", failed: "失败" },
  { locale: "en-US", previous: "Previous step", next: "Next step", copy: "Copy identifier", failed: "Failed" },
]) {
  test.describe(`step readability in ${locale}`, () => {
    test.use({ locale, viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });

    test("keeps real bar lengths with generous hit areas and navigates from the left controls", async ({ page }) => {
      await page.goto(`#tasks/${A}/preview-run-a1/1`);
      const strip = page.locator('[data-slot="detail-strip"]');
      const back = strip.getByRole("button", { name: previous });
      const forward = strip.getByRole("button", { name: next });
      await expect(back).toBeDisabled();
      await expect(forward).toBeEnabled();
      await back.locator("..").hover();
      await expect(page.getByRole("tooltip")).toContainText(previous);
      await forward.hover();
      await expect(page.getByRole("tooltip")).toContainText(next);
      await page.mouse.move(0, 0);
      const [navigation, track, arrow] = await Promise.all([
        strip.locator('[data-slot="step-navigation"]').boundingBox(),
        strip.locator('[data-slot="timeline-track"]').boundingBox(),
        back.boundingBox(),
      ]);
      expect(navigation!.x + navigation!.width).toBeLessThan(track!.x + 1);
      expect(Math.abs(arrow!.y + arrow!.height / 2 - (track!.y + track!.height / 2))).toBeLessThan(8);
      const slices = await strip.locator('[data-slot="timeline-track"] g[data-key^="span:"]').evaluateAll(groups => groups.map(group => {
        const hit = group.querySelector('[data-slot="span-hit"]');
        const bar = group.querySelector('[data-slot="execution-bar"], [data-slot="routing-bar"]');
        return { hit: hit ? (hit as SVGGraphicsElement).getBBox().width : 0, drawn: bar ? Number(bar.getAttribute("width")) : null };
      }));
      expect(slices.length).toBeGreaterThan(0);
      for (const slice of slices) {
        expect(slice.hit).toBeGreaterThanOrEqual(27.99);
        if (slice.drawn !== null) expect(slice.hit).toBeGreaterThanOrEqual(slice.drawn - 0.01);
      }
      const marks = await strip.locator('[data-slot="timeline-track"] g[data-key^="event:"], [data-slot="timeline-track"] g[data-key^="inquiry:"], [data-slot="event-cluster"]').evaluateAll(groups => groups.map(group => (group.querySelector('[data-slot="marker-hit"]') as SVGGraphicsElement).getBBox().width));
      for (const width of marks) expect(width).toBeGreaterThanOrEqual(27.99);
      await forward.click();
      await expect(page).toHaveURL(new RegExp(`/preview-run-a1/2$`));
      await expect(back).toBeEnabled();
      await back.click();
      await expect(page).toHaveURL(new RegExp(`/preview-run-a1/1$`));
      await page.goto(`#tasks/${A}/preview-run-a1/8`);
      await expect(forward).toBeDisabled();
    });

    test("puts the macro identifier in its facts row, keeps the micro identifier in its title and names failure with the shared mark", async ({ page }) => {
      await page.goto(`#tasks/${B}`);
      const macro = page.locator('[data-slot="macro-overview-header"]');
      const identifier = macro.getByRole("button", { name: `${copy} ${B}` });
      await expect(identifier).toContainText("macro-22222222…2222");
      await expect(macro.locator("[data-slot=macro-overview-facts]").getByRole("button", { name: `${copy} ${B}` })).toBeVisible();
      await page.locator('[data-slot="legend-trigger"]').click();
      const legend = page.locator('[data-slot="timeline-legend"]');
      const failure = legend.locator('[data-slot="legend-item"][data-kind="failed"]', { hasText: failed }).filter({ has: page.locator('[data-slot="conclusion-glyph"][data-kind="failed"]') });
      await expect(failure).toHaveCount(1);
      await page.goto(`#tasks/${A}/preview-run-a1/3`);
      const micro = page.locator('[data-slot="detail-header"]');
      await expect(micro.getByRole("button", { name: `${copy} preview-run-a1` })).toBeVisible();
    });

    test("writes a same-day turn interval with only one date", async ({ page }) => {
      await page.goto(`#tasks/${A}/preview-run-a1/3`);
      const interval = page.locator('[data-slot="detail-pane"] h3').first().locator("xpath=..").locator("span.font-mono.tabular-nums");
      await expect(interval.locator("time")).toHaveCount(2);
      const content = await interval.textContent();
      expect(content).toMatch(/\d{2}:\d{2}:\d{2} – \d{2}:\d{2}:\d{2}/);
      expect((content ?? "").match(/\d{2}-\d{2}/g)).toHaveLength(1);
    });
  });
}
