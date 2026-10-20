import { expect } from "@playwright/test";
import { open, test } from "./preview";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const macro = "macro-22222222-2222-4222-8222-222222222222";

const captures = fileURLToPath(new URL("../../../tmp/readability-r14", import.meta.url));
for (const theme of ["dark", "light"]) for (const detail of [false, true]) for (const width of [1440, 390]) {
  test.describe(`axis continuity ${detail ? "detail" : "timeline"} ${width}px ${theme}`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 1000 }, reducedMotion: "reduce" });

    test("keeps one baseline and a small break mark across every folded interval", async ({ page }) => {
      await page.addInitScript(theme => localStorage.setItem("hey-my-buddy.console.theme", theme), theme);
      await open(page, `#tasks/${macro}${detail ? "/preview-run-b1/3" : ""}`);
      const zoom = page.locator("[data-slot=zoom-ruler]:visible");
      await zoom.press("+"); await zoom.press("+");
      await page.evaluate(() => (document.activeElement as HTMLElement).blur());
      const surface = page.locator(detail ? '[data-slot=detail-strip]' : '[data-slot=timeline-tracks]');
      const axis = surface.locator('[data-slot=timeline-axis]');
      const track = surface.locator('[data-slot=timeline-track]').first();
      await expect(axis).toBeVisible();
      await expect(track).toBeVisible();
      await page.evaluate(() => document.fonts.ready);

      const drawing = await axis.evaluate(node => {
        const svg = node as SVGSVGElement;
        const baseline = svg.querySelector<SVGLineElement>('[data-slot=axis-baseline]')!;
        const line = baseline.getBoundingClientRect();
        const marks = [...svg.querySelectorAll<SVGPathElement>('[data-slot=axis-break-mark]')].map(mark => mark.getBoundingClientRect());
        const labels = [...svg.querySelectorAll<SVGTextElement>('[data-slot=gap-label]')].map(label => label.textContent?.trim());
        const clocks = [...svg.querySelectorAll<SVGTextElement>('[data-slot=axis-label].fill-ink2')].map(label => label.getBoundingClientRect());
        return { line: { left: line.left, right: line.right, y: line.y }, marks: marks.map(mark => ({ left: mark.left, right: mark.right, top: mark.top, bottom: mark.bottom })),
          labels, clocks: clocks.map(clock => ({ left: clock.left, right: clock.right })),
          patterns: svg.querySelectorAll("pattern").length, breakRects: svg.querySelectorAll("rect").length,
          width: Number(baseline.getAttribute("x2")) - Number(baseline.getAttribute("x1")) };
      });
      const breaks = await track.locator('[data-slot=timeline-break]').evaluateAll(nodes => nodes.map(node => {
        const box = node.getBoundingClientRect();
        return { left: box.left, right: box.right };
      }));
      expect(breaks.length).toBeGreaterThan(0);
      expect(drawing.patterns).toBe(0);
      expect(drawing.breakRects).toBe(0);
      expect(drawing.marks).toHaveLength(breaks.length);
      expect(drawing.labels.length).toBeGreaterThan(0);
      for (const gap of breaks) {
        expect(drawing.line.left).toBeLessThanOrEqual(gap.left + 1);
        expect(drawing.line.right).toBeGreaterThanOrEqual(gap.right - 1);
        expect(drawing.marks.some(mark => mark.left >= gap.left - 1 && mark.right <= gap.right + 1 && mark.top <= drawing.line.y && mark.bottom >= drawing.line.y)).toBe(true);
      }
      await mkdir(captures, { recursive: true });
      await page.mouse.move(0, 0); await page.screenshot({ path: `${captures}/axis-${detail ? "detail" : "macro"}-${theme}-${width}.png` });
      if (detail) {
        expect(drawing.clocks.length).toBeLessThanOrEqual(Math.floor(drawing.width / 180) + 2);
        for (let index = 1; index < drawing.clocks.length; index++) {
          const previous = drawing.clocks[index - 1]!, current = drawing.clocks[index]!;
          expect(Math.max(current.left - previous.right, previous.left - current.right)).toBeGreaterThanOrEqual(12);
        }
      }
    });
  });
}
