import { expect } from "@playwright/test";
import { open, test } from "./preview";
import type { MacroTaskTimeline } from "../src/macro-task-types";

const macro = "macro-11111111-1111-4111-8111-111111111111";

for (const theme of ["dark", "light"]) for (const surface of ["background", "card"]) for (const running of [false, true])
for (const harness of running ? ["zcode"] : ["codex", "claude", "zcode", "dsh"]) test.describe(`buddy palette ${theme} ${surface} ${harness} ${running ? "running" : "settled"}`, () => {
  test.use({ locale: "zh-CN", viewport: { width: 1600, height: 1800 }, reducedMotion: "reduce" });

  test("renders ten core and two extension fills with readable labels and paper-separated harness marks", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
      const response = await route.fetch();
      const timeline: MacroTaskTimeline = await response.json();
      const source = timeline.spans.find(span => span.kind === "execution")!;
      const row = timeline.rows.find(row => row.runId === source.runId)!;
      timeline.rows = Array.from({ length: 12 }, (_, index) => ({ ...row, runId: `palette-${index}`, parentRunId: null,
        rootRunId: `palette-${index}`, depth: 0, rootLeadRunId: null, state: running ? "executing" : "accepted",
        title: `Buddy ${index + 1}`, titleSource: "title", delegationAuthority: surface === "card" }));
      timeline.spans = timeline.rows.map((row, index) => ({ ...source, runId: row.runId, spanId: `palette-span-${index}`,
        startAt: new Date(Date.parse(source.startAt!) + index * 1000).toISOString(),
        endAt: running ? null : source.endAt, state: running ? "executing" : "finished", uncertain: false,
        shutdownConfirmed: !running, resultStatus: running ? undefined : "ok", expectedSeconds: null,
        reminders: null, inquiries: [], buddy: { ...source.buddy!, harness, model: `buddy-${index}` } }));
      timeline.events = [];
      timeline.totals.rows = timeline.totals.allRows = 12;
      await route.fulfill({ response, json: timeline });
    });
    await open(page, `#tasks/${macro}`);
    await expect(page.locator('[data-slot=execution-bar]')).toHaveCount(12);
    await expect(page.locator('[data-slot=track-label][class*=fill-execution-ink]')).toHaveCount(12);
    const measurements = await page.locator('[data-slot=timeline-track]').evaluateAll(tracks => {
      const context = document.createElement("canvas").getContext("2d", { willReadFrequently: true })!;
      const luminance = (color: string) => {
        context.clearRect(0, 0, 1, 1); context.fillStyle = color; context.fillRect(0, 0, 1, 1);
        const rgb = [...context.getImageData(0, 0, 1, 1).data].slice(0, 3).map(value => value / 255)
          .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
        return rgb[0]! * 0.2126 + rgb[1]! * 0.7152 + rgb[2]! * 0.0722;
      };
      const contrast = (a: string, b: string) => (Math.max(luminance(a), luminance(b)) + 0.05) / (Math.min(luminance(a), luminance(b)) + 0.05);
      return tracks.filter(track => track.querySelector('[data-slot=execution-bar]')).map(track => {
        const fill = getComputedStyle(track.querySelector('[data-slot=execution-bar]')!).fill;
        const ink = getComputedStyle(track.querySelector('[data-slot=track-label][class*=fill-execution-ink]')!).fill;
        const foreign = track.querySelector<SVGForeignObjectElement>('[data-slot=execution-buddy]')!;
        const icon = foreign.querySelector<HTMLElement>('img, [data-slot=harness-logo]')!;
        const paper = track.querySelector<SVGRectElement>('[data-slot=execution-buddy-paper]')!;
        const paperBox = paper.getBoundingClientRect(), iconBox = icon.getBoundingClientRect();
        const margins = [iconBox.left - paperBox.left, iconBox.top - paperBox.top, paperBox.right - iconBox.right, paperBox.bottom - iconBox.bottom];
        const samples = [new DOMPoint((paperBox.left + iconBox.left) / 2, iconBox.top + iconBox.height / 2),
          new DOMPoint((paperBox.right + iconBox.right) / 2, iconBox.top + iconBox.height / 2),
          new DOMPoint(iconBox.left + iconBox.width / 2, (paperBox.top + iconBox.top) / 2),
          new DOMPoint(iconBox.left + iconBox.width / 2, (paperBox.bottom + iconBox.bottom) / 2)];
        let ancestor = track.parentElement!;
        while (getComputedStyle(ancestor).backgroundColor === "rgba(0, 0, 0, 0)") ancestor = ancestor.parentElement!;
        const paperFill = getComputedStyle(paper).fill, paperInk = getComputedStyle(foreign).color;
        const isolation = { margins, samplesOnPaper: samples.every(point => paper.isPointInFill(point.matrixTransform(paper.getScreenCTM()!.inverse()))),
          fill: paperFill, background: getComputedStyle(ancestor).backgroundColor, opacity: getComputedStyle(paper).opacity,
          paperFirst: paper.nextElementSibling === foreign, contrast: contrast(paperFill, paperInk),
          monochromeInk: icon.dataset.slot === "harness-logo" ? getComputedStyle(icon).backgroundColor : null, paperInk };
        const leading = track.querySelector('linearGradient stop[offset="1"]');
        return { fill, ink, isolation, contrast: contrast(fill, ink), edgeContrast: leading ? contrast(getComputedStyle(leading).stopColor, ink) : null };
      });
    });
    expect(measurements).toHaveLength(12);
    expect(new Set(measurements.map(item => item.fill)).size).toBe(12);
    expect(new Set(measurements.map(item => item.ink)).size).toBe(2);
    for (const item of measurements) {
      expect(Math.min(...item.isolation.margins)).toBeGreaterThanOrEqual(1.9);
      expect(item.isolation.samplesOnPaper).toBe(true);
      expect(item.isolation.fill).toBe(item.isolation.background);
      expect(item.isolation.opacity).toBe("1");
      expect(item.isolation.paperFirst).toBe(true);
      expect(item.isolation.contrast).toBeGreaterThanOrEqual(4.5);
      if (item.isolation.monochromeInk !== null) expect(item.isolation.monochromeInk).toBe(item.isolation.paperInk);
      expect(item.contrast).toBeGreaterThanOrEqual(4.5);
      if (item.edgeContrast !== null) expect(item.edgeContrast).toBeGreaterThanOrEqual(4.5);
    }
  });
});
