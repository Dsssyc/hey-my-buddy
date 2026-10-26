import { expect } from "@playwright/test";
import { fileURLToPath } from "node:url";
import type { MacroTaskTimeline, TimelineEvent } from "../src/macro-task-types";
import { open, openLegend, settle, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const at = (minutes: number) => new Date(Date.UTC(2026, 8, 27, 6) + minutes * 60_000).toISOString();
const captures = fileURLToPath(new URL("../../../tmp/timeline-refine/", import.meta.url));

/** The same synthetic facts drive the before/after captures and the geometry checks. */
async function refineFixture(page: import("@playwright/test").Page, clustered = false) {
  await page.clock.setFixedTime(new Date("2026-09-27T10:00:00.000Z"));
  await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
    const response = await route.fetch();
    const timeline = await response.json() as MacroTaskTimeline;
    const ids = ["preview-run-a1", "preview-run-a1-h1", "preview-run-a2", "preview-run-a3"];
    timeline.rows = ids.map(runId => timeline.rows.find(row => row.runId === runId)!);
    const [handoff, failed, settled, waiting] = timeline.rows;
    for (const row of timeline.rows) {
      row.createdAt = at(0); row.acceptedAt = null; row.reviewRejectedAt = null;
      row.rootLeadRunId = null;
    }
    handoff!.title = "交接与打回：历史身份与当前 buddy"; handoff!.state = "accepted"; handoff!.acceptedAt = at(80);
    failed!.title = "失败窄条：一秒执行与改派标记"; failed!.state = "failed";
    settled!.title = "等待已结束：交给 Host 后保留菱形"; settled!.state = "accepted"; settled!.acceptedAt = at(120);
    waiting!.title = "仍在等 Host：橙色虚线延伸到现在"; waiting!.state = "delivered";
    const seed = timeline.spans.find(span => span.spanId === "execution:att-a1-2")!;
    const span = (runId: string, spanId: string, start: number, end: number, buddy = timeline.rows.find(row => row.runId === runId)!.buddy) => ({
      ...seed, runId, spanId, startAt: at(start), endAt: at(end), buddy: buddy ? { ...buddy, effort: null } : null, expectedSeconds: null,
      inquiries: [], reminders: null, error: undefined, resultStatus: "ok" as const,
    });
    timeline.spans = [
      span(handoff!.runId, "history", 5, 15, { ...handoff!.buddy!, harness: "claude", model: "claude-opus-5-5" }),
      span(handoff!.runId, "execution:att-a1-2", 40, 48),
      { ...span(failed!.runId, "failed-narrow", 22, 22 + 1 / 60, { ...failed!.buddy!, harness: "codex" }), resultStatus: "failed" },
      span(settled!.runId, "settled", 5, 12),
      { ...span(settled!.runId, "host-settled", 12, 100), kind: "host", state: "approved", buddy: null, inquiries: null },
      span(waiting!.runId, "waiting", 130, 135),
    ];
    const event = (seq: number, kind: string, minutes: number, extra: Partial<TimelineEvent> = {}): TimelineEvent => ({
      ...timeline.events[0]!, seq, kind, at: at(minutes), runId: handoff!.runId,
      note: null, verdict: null, decision: null, strategy: null, state: null,
      artifactId: null, integrationId: null, continuationId: null, eventKind: `workflow.${kind}`, ...extra,
    });
    timeline.events = [
      event(900, "reject", 25, { eventKind: "workflow.review_rejected", note: "修复边界后重做", verdict: "rejected", artifactId: "art-review" }),
      event(901, "continue", 25 + 1 / 60000, { eventKind: "workflow.continued", continuationId: "cont-review" }),
      event(910, "integrate", 80, { integrationId: "int-review", artifactId: "art-output", strategy: "cherry-pick", state: "verified", note: "整合验证通过" }),
      event(911, "accept", 80 + 1 / 60000, { integrationId: "int-review", artifactId: "art-output", verdict: "accepted", note: "验收完成" }),
    ];
    if (clustered) for (const event of timeline.events) event.at = at(25);
    timeline.totals = { ...timeline.totals, rows: timeline.rows.length, spans: timeline.spans.length, events: timeline.events.length };
    await route.fulfill({ response, json: timeline, headers: { ...response.headers(), date: new Date(timeline.observedAt).toUTCString() } });
  });
}

for (const theme of ["dark", "light"] as const) test.describe(`timeline refinement ${theme}`, () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  test("keeps identities, paired actions, narrow failures and wait origins readable", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await refineFixture(page);
    const read = page.waitForResponse(response => new URL(response.url()).pathname === `/api/macro-tasks/${macro}/timeline`);
    await open(page, `#tasks/${macro}`);
    const timeline = await (await read).json() as MacroTaskTimeline;
    for (const span of timeline.spans.filter(span => span.kind === "execution")) {
      expect(span.buddy!.effort).toBeNull();
      expect(timeline.rows.find(row => row.runId === span.runId)!.buddy!.effort).toBeTruthy();
    }
    await expect(page.locator('[data-key="span:failed-narrow"]')).toBeVisible();
    await settle(page);
    await page.mouse.move(0, 0);
    await page.screenshot({ path: `${captures}/${process.env.HMB_TIMELINE_CAPTURE_BEFORE ? "before" : "after"}-${theme}.png` });
    if (process.env.HMB_TIMELINE_CAPTURE_BEFORE) return;
    await expect(page.locator('[data-key="span:execution:att-a1-2"] [data-slot=execution-buddy]')).toHaveCount(0);
    await expect(page.locator('[data-key="span:history"] [data-slot=execution-buddy]')).toHaveCount(1);
    await expect(page.locator('[data-key="span:failed-narrow"] [data-slot=execution-buddy]')).toHaveCount(0);
    await expect(page.locator('[data-slot=event-cluster]')).toHaveCount(0);
    await expect(page.locator('[data-key="event:900"]')).toHaveAttribute("aria-label", /打回.*修复边界后重做.*续接/);
    await expect(page.locator('[data-key="event:911"]')).toHaveAttribute("aria-label", /验收完成.*整合.*整合验证通过/);
    await expect(page.locator('[data-key="span:host-settled"] [data-slot=host-wait]')).not.toHaveAttribute("stroke-dasharray");
    const geometry = await page.locator('[data-slot=timeline-track]').evaluateAll(tracks => tracks.map(track => {
      const bars = [...track.querySelectorAll<SVGRectElement>('[data-slot=execution-bar]')].map(bar => bar.getBBox());
      const marks = [...track.querySelectorAll<SVGRectElement>('[data-slot=execution-buddy-paper]')].map(mark => {
        const own = mark.parentElement!.querySelector<SVGRectElement>('[data-slot=execution-bar]')!.getBBox();
        const box = mark.getBBox();
        return { width: box.width, contained: box.x >= own.x && box.x + box.width <= own.x + own.width,
          clear: bars.every(bar => box.x + box.width <= bar.x || box.x >= bar.x + bar.width) };
      });
      const crosses = [...track.querySelectorAll<SVGPathElement>('[data-slot=execution-failed]')].map(cross => {
        const own = cross.closest('[data-key]')!.querySelector<SVGRectElement>('[data-slot=execution-bar]')!.getBBox();
        const box = cross.getBBox();
        const paper = cross.previousElementSibling as SVGPathElement;
        const halfStroke = parseFloat(getComputedStyle(paper).strokeWidth) / 2;
        return { barWidth: own.width, barX: own.x, crossX: box.x, crossWidth: box.width, halfStroke, inside: box.x - halfStroke >= own.x && box.x + box.width + halfStroke <= own.x + own.width,
          outside: box.x - halfStroke > own.x + own.width + 3.5 };
      });
      const waits = [...track.querySelectorAll<SVGLineElement>('[data-slot=host-wait], [data-slot=acceptance-wait]')].map(wait => ({
        dashed: wait.hasAttribute('stroke-dasharray'),
        diamond: [...track.querySelectorAll<SVGPathElement>('[data-slot=waiting-diamond]')].some(diamond => Math.abs(diamond.getBBox().x + diamond.getBBox().width / 2 - wait.x1.baseVal.value) < 0.01),
      }));
      return { marks, crosses, waits };
    }));
    expect(geometry.flatMap(track => track.marks).length).toBeGreaterThan(0);
    for (const mark of geometry.flatMap(track => track.marks)) expect(mark.contained || mark.clear, "a mark must fit wholly inside a bar or wholly beside it").toBe(true);
    const crosses = geometry.flatMap(track => track.crosses);
    expect(crosses).toHaveLength(1); expect(crosses[0]!.barWidth).toBeLessThan(1);
    for (const cross of crosses) expect(cross.barWidth < 14 ? cross.outside : cross.inside, JSON.stringify(cross)).toBe(true);
    for (const wait of geometry.flatMap(track => track.waits)) expect(wait.diamond).toBe(true);
    expect(geometry.flatMap(track => track.waits).some(wait => wait.dashed)).toBe(true);
    const breaks = await page.locator('[data-slot=timeline-break]').evaluateAll(elements => elements.map(element => (element as SVGRectElement).getBBox().width));
    expect(breaks.length).toBeGreaterThan(0);
    for (const width of breaks) expect(width).toBeCloseTo(54);
    const legend = await openLegend(page);
    await expect(legend.locator('[data-kind=waiting]')).toContainText("菱形标出工作交给 Host 或 Lead 的地方，虚线表示仍在等");
    const patterns = await page.locator('[data-slot=idle-pattern]').evaluateAll(elements => elements.map(pattern => ({
      width: pattern.getAttribute("width"), height: pattern.getAttribute("height"),
      path: pattern.querySelector("path")!.getAttribute("d"), stroke: getComputedStyle(pattern.querySelector("path")!).stroke,
      opacity: getComputedStyle(pattern.querySelector("path")!).strokeOpacity,
    })));
    expect(patterns.length).toBeGreaterThan(1);
    expect(new Set(patterns.map(pattern => JSON.stringify(pattern))).size).toBe(1);
    expect(patterns[0]!.width).toBe("14"); expect(Number(patterns[0]!.opacity)).toBe(0.4);
    const diamonds = await page.locator('[data-slot=waiting-diamond]').evaluateAll(elements => elements.map(diamond => ({ fill: getComputedStyle(diamond).fill, width: (diamond as SVGPathElement).getBBox().width })));
    expect(new Set(diamonds.map(diamond => JSON.stringify(diamond))).size).toBe(1);
    await page.keyboard.press("Escape");
    await page.locator('[data-key="span:execution:att-a1-2"]').click();
    const strip = page.locator('[data-slot=detail-strip]');
    await expect(strip).toBeVisible();
    await expect(strip.locator('[data-key="span:execution:att-a1-2"] [data-slot=execution-buddy]')).toHaveCount(0);
    await expect(strip.locator('[data-key="span:history"] [data-slot=execution-buddy]')).toHaveCount(1);
    await expect(strip.locator('[data-slot=event-mark]')).toHaveCount(1);
    await expect(strip.locator('[data-slot=accept-mark]')).toHaveCount(1);
  });
  test("counts paired actions once and shows all their facts in the cluster", async ({ page }) => {
    test.skip(!!process.env.HMB_TIMELINE_CAPTURE_BEFORE, "before captures preserve the old drawing");
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await refineFixture(page, true);
    await open(page, `#tasks/${macro}`);
    const cluster = page.locator('[data-slot=event-cluster]');
    await expect(cluster).toHaveCount(1);
    await expect(cluster.locator('[data-slot=cluster-mark] text')).toHaveText("2");
    await cluster.click();
    const entries = page.locator('[data-slot=popover-content] button');
    await expect(entries).toHaveCount(2);
    await expect(entries.nth(0)).toContainText("修复边界后重做");
    await expect(entries.nth(0)).toContainText("续接");
    await expect(entries.nth(1)).toContainText("整合验证通过");
    await expect(entries.nth(1)).toContainText("验收完成");
    await page.screenshot({ path: `${captures}/cluster-${theme}.png` });
  });
});
