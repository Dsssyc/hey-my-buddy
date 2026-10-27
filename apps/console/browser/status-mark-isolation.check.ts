import { expect } from "@playwright/test";
import { open, test } from "./preview";
import type { MacroTaskTimeline } from "../src/macro-task-types";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const outcomes = ["failed", "unknown", "cancelled"] as const;

for (const theme of ["dark", "light"]) test.describe(`status separation ${theme}`, () => {
  test.use({ locale: "zh-CN", viewport: { width: 1600, height: 1200 }, reducedMotion: "reduce" });

  test("separates every core fill from failed, unconfirmed and dashed state marks with rendered track paper", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
      const response = await route.fetch();
      const timeline: MacroTaskTimeline = await response.json();
      const source = timeline.spans.find(span => span.kind === "execution")!;
      const row = timeline.rows.find(row => row.runId === source.runId)!;
      timeline.rows = Array.from({ length: 12 }, (_, index) => ({ ...row, runId: `status-${index}`, parentRunId: null,
        rootRunId: `status-${index}`, rootLeadRunId: null, depth: 0, title: `Buddy ${index + 1}`, titleSource: "title" }));
      timeline.spans = timeline.rows.flatMap((row, index) => outcomes.map((outcome, phase) => ({
        ...source, runId: row.runId, spanId: `status-${index}-${outcome}`,
        startAt: new Date(Date.parse(source.startAt!) + phase * 600000 + index * 1000).toISOString(),
        endAt: new Date(Date.parse(source.startAt!) + phase * 600000 + 420000 + index * 1000).toISOString(),
        state: "finished", uncertain: outcome === "unknown", shutdownConfirmed: outcome !== "unknown",
        resultStatus: outcome === "failed" ? "failed" : outcome === "cancelled" ? "cancelled" : undefined,
        disposition: "completed", expectedSeconds: null,
        reminders: null, inquiries: [], buddy: { ...source.buddy!, model: `status-buddy-${index}` },
      })));
      timeline.events = [];
      timeline.totals.rows = timeline.totals.allRows = 12;
      await route.fulfill({ response, json: timeline });
    });
    await open(page, `#tasks/${macro}`);
    for (const outcome of outcomes) await expect(page.locator(`[data-slot=execution-bar][data-outcome=${outcome}]`)).toHaveCount(12);
    const measured = await page.locator('[data-slot=execution-bar]').evaluateAll(bars => bars.map(node => {
      const bar = node as SVGRectElement;
      const segment = bar.parentElement!;
      const frame = segment.querySelector<SVGPathElement>('[data-slot=execution-status-outline]')!;
      const paper = frame.previousElementSibling as SVGPathElement;
      const fillBox = bar.getBBox(), frameBox = frame.getBBox();
      const width = Number(getComputedStyle(frame).strokeWidth.replace("px", ""));
      // Browser geometry includes the actual path and stroke used by the renderer.
      const gap = Math.min(fillBox.x - frameBox.x, fillBox.y - frameBox.y,
        frameBox.x + frameBox.width - fillBox.x - fillBox.width,
        frameBox.y + frameBox.height - fillBox.y - fillBox.height) - width / 2;
      const point = new DOMPoint(fillBox.x + fillBox.width / 2, fillBox.y - gap / 2);
      const track = bar.closest('[data-slot=timeline-track]')!;
      let ancestor = track.parentElement!;
      while (getComputedStyle(ancestor).backgroundColor === "rgba(0, 0, 0, 0)") ancestor = ancestor.parentElement!;
      const marks = [...segment.querySelectorAll<SVGPathElement>('[data-slot=execution-status-outline], [data-slot=execution-failed], [data-slot=unconfirmed-end]')].map(mark => {
        const ground = mark.previousElementSibling as SVGPathElement;
        const style = getComputedStyle(mark), groundStyle = getComputedStyle(ground);
        let onFill = 0;
        if (mark !== frame) {
          const length = mark.getTotalLength();
          for (let fraction = 0.05; fraction < 0.96; fraction += 0.05) {
            const at = length * fraction;
            const p = mark.getPointAtLength(at), before = mark.getPointAtLength(at - 0.1), after = mark.getPointAtLength(at + 0.1);
            const dx = after.x - before.x, dy = after.y - before.y, size = Math.hypot(dx, dy);
            const offset = Number(style.strokeWidth.replace("px", "")) / 2 + 0.6;
            for (const side of [-1, 1]) {
              const beside = new DOMPoint(p.x - side * dy / size * offset, p.y + side * dx / size * offset);
              if (bar.isPointInFill(beside) && ground.isPointInStroke(beside) && !mark.isPointInStroke(beside)) onFill++;
            }
          }
        }
        return { slot: mark.dataset.slot, onFill, paper: groundStyle.stroke, paperWidth: Number(groundStyle.strokeWidth.replace("px", "")),
          width: Number(style.strokeWidth.replace("px", "")), opacity: groundStyle.opacity,
          box: ground.getBBox().width, pathMatches: ground.getAttribute("d") === mark.getAttribute("d") };
      });
      return { outcome: bar.dataset.outcome, fill: getComputedStyle(bar).fill, gap,
        gapIsPaper: paper.isPointInStroke(point) && !frame.isPointInStroke(point) && !bar.isPointInFill(point),
        background: getComputedStyle(ancestor).backgroundColor, dashed: frame.getAttribute("stroke-dasharray"), marks };
    }));
    expect(measured).toHaveLength(36);
    expect(new Set(measured.map(item => item.fill)).size).toBe(12);
    for (const item of measured) {
      expect(item.gap).toBeGreaterThanOrEqual(1.49);
      expect(item.gapIsPaper).toBe(true);
      expect(item.dashed).toBe(item.outcome === "failed" ? null : "3 2");
      expect(item.marks).toHaveLength(["failed", "unknown"].includes(item.outcome!) ? 2 : 1);
      for (const mark of item.marks) {
        expect(mark.paper).toBe(item.background);
        expect(mark.opacity).toBe("1");
        expect(mark.box).toBeGreaterThan(0);
        expect(mark.pathMatches).toBe(true);
        expect(mark.paperWidth - mark.width).toBeGreaterThanOrEqual(3);
        if (mark.slot !== "execution-status-outline") expect(mark.onFill).toBeGreaterThan(4);
      }
    }
  });
});
