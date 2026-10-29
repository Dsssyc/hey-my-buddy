import { expect, type Locator } from "@playwright/test";
import { open, settle, test } from "./preview";

const macro = "macro-22222222-2222-4222-8222-222222222222";
type Sample = { atMs: number; name: string; track: string | null; tail: string | null };
type SampledRow = HTMLElement & { highlightSamples: Promise<Sample[]> };

/** Start in the input event, before React updates, so automation cannot skip the fade. */
async function sampleHighlight(row: Locator, event: "pointerover" | "focusin") {
  await row.evaluate((node, event) => {
    const row = node as SampledRow;
    const name = row.querySelector('[data-slot=timeline-row-name]')!;
    const track = row.querySelector('[data-slot=timeline-track]')!;
    const last = row.matches('[data-slot=timeline-lane-lead]') ? row.lastElementChild! : track.parentElement!.lastElementChild!;
    const tail = last === track || last.contains(track) ? null : last;
    row.highlightSamples = new Promise(resolve => {
      row.addEventListener(event, () => {
        const start = performance.now();
        const samples: Sample[] = [];
        const sample = () => {
          const wash = track.querySelector('[data-slot=selected-row]');
          samples.push({ atMs: performance.now() - start, name: getComputedStyle(name).backgroundColor,
            track: wash ? getComputedStyle(wash).fill : null, tail: tail ? getComputedStyle(tail).backgroundColor : null });
          if (performance.now() - start < 225) requestAnimationFrame(sample);
          else resolve(samples);
        };
        requestAnimationFrame(sample);
      }, { capture: true, once: true });
    });
  }, event);
}

for (const theme of ["dark", "light"] as const) for (const width of [1440, 390]) {
  test.describe(`timeline highlight ${theme} ${width}`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 1000 }, reducedMotion: "no-preference" });
    test("paints every part of a hovered, focused or selected row together without a fade", async ({ page }, testInfo) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/${macro}`);
      expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(false);
      const wash = theme === "light" ? "rgba(0, 0, 0, 0.04)" : "rgba(255, 255, 255, 0.05)";
      const rows = [
        { kind: "Lead header", row: page.locator('[data-slot=timeline-lane-lead]').first() },
        { kind: "lane member", row: page.locator('[data-slot=timeline-lane] [data-slot=timeline-row]').first() },
        { kind: "ordinary row", row: page.locator('[data-slot=timeline-canvas] > [data-slot=timeline-row]').first() },
      ];
      for (const { kind, row } of rows) {
        const name = row.locator('[data-slot=timeline-row-name]');
        const track = row.locator('[data-slot=timeline-track]');
        for (const source of ["track hover", "name hover", "focus"] as const) await test.step(`${kind}: ${source}`, async () => {
          await row.scrollIntoViewIfNeeded();
          await page.mouse.move(width - 4, 4);
          await page.locator('[data-slot=zoom-ruler]').focus();
          await settle(page);
          await expect(track.locator('[data-slot=selected-row]')).toHaveCount(0);
          await sampleHighlight(row, source === "focus" ? "focusin" : "pointerover");
          if (source === "focus") await name.focus();
          else await (source === "track hover" ? track : name).hover();
          const samples = await row.evaluate(node => (node as SampledRow).highlightSamples);
          await testInfo.attach(`${kind}-${source}`, { body: JSON.stringify(samples, null, 2), contentType: "application/json" });
          const firstHighlightFrame = samples.findIndex(sample => sample.track !== null);
          const nameFrame = samples.findIndex(sample => sample.name === wash);
          const trackFrame = samples.findIndex(sample => sample.track === wash);
          console.log(`${kind}, ${source}: name=${samples[nameFrame]?.atMs.toFixed(2)}ms, track=${samples[trackFrame]?.atMs.toFixed(2)}ms`);
          // Observe the complete wash, allowing React to commit after the input event.
          expect(firstHighlightFrame, "the input paints a highlight").toBeGreaterThanOrEqual(0);
          expect(nameFrame, "the name is complete on the first highlighted frame").toBe(firstHighlightFrame);
          expect(trackFrame, "the track is complete on the same frame").toBe(firstHighlightFrame);
          for (const sample of samples.slice(firstHighlightFrame)) {
            expect(sample.name).toBe(wash);
            expect(sample.track).toBe(wash);
            if (sample.tail !== null) expect(sample.tail, "the rest of the row shares the same wash").toBe(wash);
          }
        });
        // Blank track selects the row; the wash remains when pointer and focus leave.
        const box = (await track.boundingBox())!;
        await page.mouse.click(box.x + box.width / 2, box.y + box.height - 2);
        await page.mouse.move(width - 4, 4);
        await page.locator('[data-slot=zoom-ruler]').focus();
        await expect(page.locator('[data-slot=task-detail]')).toHaveCount(0);
        await expect(name).toHaveCSS("background-color", wash);
        await expect(track.locator('[data-slot=selected-row]')).toHaveCSS("fill", wash);
      }
    });
  });
}
