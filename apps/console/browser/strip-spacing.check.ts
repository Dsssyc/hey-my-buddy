import { expect, type Locator } from "@playwright/test";
import { expandDetailTimeline, open, settle, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const run = "preview-run-a1";

/** Measure the rendered text boxes, including font descent, against the slice tops. */
async function spacing(track: Locator, axis: Locator) {
  const ruler = (await axis.boundingBox())!;
  return track.evaluate((node, rulerBottom) => {
    const frame = node.getBoundingClientRect();
    const bars = [...node.querySelectorAll("[data-slot=execution-bar], [data-slot=routing-bar]")]
      .map(bar => bar.getBoundingClientRect());
    const labels = [...node.querySelectorAll('[data-slot=track-label][y="12"]')].map(label => {
      const box = label.getBoundingClientRect();
      return { text: label.textContent, gap: Math.min(...bars.map(bar => bar.top)) - box.bottom,
        withinTrack: box.top >= frame.top && box.bottom <= frame.bottom,
        clearOfRuler: box.top >= rulerBottom };
    });
    return { bars: bars.length, labels };
  }, ruler.y + ruler.height);
}

for (const locale of ["zh-CN", "en-US"]) for (const theme of ["dark", "light"]) for (const width of [1440, 390]) {
  test.describe(`strip label spacing ${locale} ${theme} ${width}px`, () => {
    test.use({ locale, viewport: { width, height: 1000 }, reducedMotion: "reduce" });

    test("matches the macro row's label gap, clears slices and ruler, and keeps its height", async ({ page }) => {
      await page.clock.setFixedTime(new Date("2026-09-27T10:00:00.000Z"));
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/${macro}`);
      const macroTrack = page.locator(`[data-slot=timeline-row][data-run-id=${run}] [data-slot=timeline-track]`);
      await expect(macroTrack).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      const reference = await spacing(macroTrack, page.locator("[data-slot=timeline-axis]"));
      expect(reference.bars).toBeGreaterThan(0);
      expect(reference.labels.length).toBeGreaterThan(0);
      for (const label of reference.labels) {
        expect(label.withinTrack && label.clearOfRuler, label.text ?? "macro label").toBe(true);
        expect(label.gap, label.text ?? "macro label").toBeGreaterThanOrEqual(0);
      }

      await page.evaluate(hash => { window.location.hash = hash; }, `#tasks/${macro}/${run}/2`);
      await expandDetailTimeline(page);
      const strip = page.locator("[data-slot=detail-strip]");
      const track = strip.locator("[data-slot=timeline-track]");
      await expect(track).toBeVisible();
      await settle(page);
      const detail = await spacing(track, strip.locator("[data-slot=timeline-axis]"));
      expect(detail.bars).toBeGreaterThan(0);
      expect(detail.labels.length).toBeGreaterThan(0);
      for (const label of detail.labels) {
        expect(label.withinTrack && label.clearOfRuler, label.text ?? "detail label").toBe(true);
        expect(label.gap, `${label.text} does not overlap the slices`).toBeGreaterThanOrEqual(0);
        for (const other of reference.labels)
          expect(Math.abs(label.gap - other.gap), `${label.text} / ${other.text}`).toBeLessThanOrEqual(1);
      }

      const height = (await strip.boundingBox())!.height;
      await strip.getByRole("button", { name: locale === "zh-CN" ? "下一步" : "Next step", exact: true }).click();
      await settle(page);
      expect((await strip.boundingBox())!.height).toBe(height);
      const ruler = strip.locator("[data-slot=zoom-ruler]");
      await ruler.press("+");
      await ruler.press("0");
      await settle(page);
      expect((await strip.boundingBox())!.height).toBe(height);
    });
  });
}
