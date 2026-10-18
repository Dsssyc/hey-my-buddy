import { expect, type Page } from "@playwright/test";
import type { MacroTaskTimeline, TimelineEvent } from "../src/macro-task-types";
import { open, screenshots as shots, test } from "./preview";

const MACRO_A = "macro-11111111-1111-4111-8111-111111111111";
const MACRO_B = "macro-22222222-2222-4222-8222-222222222222";
const selectedRow = (page: Page, runId: string) => page.locator(`[data-slot=timeline-row][data-run-id="${runId}"], [data-slot=timeline-lane-lead][data-run-id="${runId}"]`);

for (const locale of ["zh-CN", "en-US"] as const) for (const theme of ["light", "dark"] as const) for (const width of [1440, 390]) {
  test.describe(`timeline drawing decision ${locale} ${theme} ${width}px`, () => {
    test.use({ locale, viewport: { width, height: width === 390 ? 844 : 1000 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    });

    test("fills a named row through idle breaks and outlines only the clicked item", async ({ page }) => {
      await open(page, `#tasks/${MACRO_B}`);
      const name = page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-b1"]:visible');
      const track = selectedRow(page, "preview-run-b1");
      await name.click();
      await expect(page.locator('[data-slot=detail-strip]')).toBeVisible();
      await page.locator('[data-slot=detail-header]').getByRole("button", { name: locale === "zh-CN" ? "收起详情" : "Close detail" }).click();
      await expect(track.locator("[data-slot=selected-row]")).toHaveCount(1);
      await expect(track.locator("[data-slot=selected-item]")).toHaveCount(0);
      const drawing = await track.evaluate(node => {
        const wash = node.querySelector<SVGRectElement>("[data-slot=selected-row]")!;
        const svg = wash.ownerSVGElement!;
        const tracks = node.closest("[data-slot=timeline-tracks]")!;
        const box = wash.getBoundingClientRect(), viewport = tracks.getBoundingClientRect();
        const gaps = Array.from(svg.querySelectorAll<SVGRectElement>("[data-slot=timeline-break]"));
        const fill = getComputedStyle(wash).fill.match(/[\d.]+/g)?.slice(0, 3).map(Number) ?? [];
        return { left: box.left, right: box.right, viewportLeft: viewport.left, viewportRight: viewport.right,
          trackLeft: svg.getBoundingClientRect().left, gapCount: gaps.length, coversGaps: gaps.every(gap => {
            const rect = gap.getBoundingClientRect();
            return box.left <= rect.left && box.right >= rect.right && !!(gap.compareDocumentPosition(wash) & Node.DOCUMENT_POSITION_FOLLOWING);
          }), fill };
      });
      expect(drawing.left).toBeCloseTo(drawing.trackLeft, 0);
      expect(drawing.left).toBeLessThanOrEqual(drawing.viewportLeft + (width > 760 ? 272 : 0) + 2);
      expect(drawing.right).toBeGreaterThanOrEqual(drawing.viewportRight - 2);
      expect(drawing.gapCount).toBeGreaterThan(0);
      expect(drawing.coversGaps).toBe(true);
      expect(drawing.fill).toHaveLength(3);
      expect(Math.max(...drawing.fill) - Math.min(...drawing.fill)).toBeLessThanOrEqual(10);
      if (locale === "zh-CN") await page.screenshot({ path: `${shots}/name-selected-${theme}-${width}.png` });

      // At the narrow fit scale, adjacent short queue and execution hit areas overlap.
      if (width === 390) for (let n = 0; n < 2; n++) await page.locator('[data-slot=timeline-zoom] button').nth(1).click();
      const item = track.locator('[data-key="span:execution:att-b1-2"]');
      await item.click();
      await expect(track.locator("[data-slot=selected-row]")).toHaveCount(1);
      await expect(track.locator("[data-slot=selected-item]")).toHaveCount(1);
      await expect(item.locator("[data-slot=selected-item]")).toHaveCount(1);
      await expect(page.locator("[data-slot=timeline-track] [data-slot=selected-item]")).toHaveCount(1);
      await item.focus();
      await expect(item.locator('[data-slot=fragment-focus]')).toHaveCSS("opacity", "0");
      await expect(item.locator('[data-slot=selected-item]')).toHaveCSS("stroke-width", "1px");
      if (locale === "zh-CN") await page.screenshot({ path: `${shots}/span-selected-${theme}-${width}.png` });

      const wait = track.locator('[data-key="span:queue:att-b1-1"]');
      await wait.click();
      await expect(track.locator("[data-slot=selected-item]")).toHaveCount(1);
      await expect(wait.locator("[data-slot=selected-item]")).toHaveCount(1);
      await page.locator('[data-slot=timeline-legend]').hover();
      await expect(page.locator('[data-slot=timeline-status]')).toContainText(locale === "zh-CN" ? "排队" : "Queue");
      const marker = track.locator(`[data-key^="event:"][aria-label*="${locale === "zh-CN" ? "续接" : "Continuation"}"]`);
      await marker.click();
      await expect(track.locator("[data-slot=selected-item]")).toHaveCount(1);
      await expect(marker.locator("[data-slot=selected-item]")).toHaveCount(1);
      await page.locator('[data-slot=timeline-legend]').hover();
      await expect(page.locator('[data-slot=timeline-status]')).toContainText(locale === "zh-CN" ? "续接" : "Continuation");
    });

    test("keeps event names beside dots, glyphs unlabelled, and timeline text unselectable", async ({ page }) => {
      // Give the geometry check one isolated, known event. The normal fixture's
      // clustered decision and continuation may correctly have no visible label.
      await page.route(`**/api/macro-tasks/${MACRO_A}/timeline*`, async route => {
        const response = await route.fetch();
        const timeline = await response.json() as MacroTaskTimeline;
        const seed = timeline.events.find(event => event.kind === "accept")!;
        const marker = (seq: number, runId: string, kind: string, at: string): TimelineEvent => ({
          seq, runId, kind, at, actor: seed.actor, decision: null, verdict: null, state: null, strategy: null,
          note: null, attemptId: null, requestId: null, artifactId: null,
        });
        timeline.events.push(marker(9001, "preview-run-a1", "takeover", new Date(Date.parse(seed.at) + 25 * 60_000).toISOString()));
        // A name at the end of an execution must not jump past its acceptance connector.
        const end = timeline.spans.find(span => span.spanId === "execution:att-h1-2")!.endAt!;
        timeline.events.push(marker(9002, "preview-run-a1-h1", "continue", end));
        timeline.events.push(marker(9003, "preview-run-a1", "takeover", new Date(Date.parse(timeline.observedAt) - 60_000).toISOString()));
        timeline.totals.events += 3;
        await route.fulfill({ response, json: timeline });
      });
      await open(page, `#tasks/${MACRO_A}`);
      await expect(page.locator("[data-slot=timeline-axis]")).toBeVisible();
      await expect(page.locator('text[data-event-key="event:9001"]')).toBeVisible();
      await expect(page.locator('text[data-event-key="event:9002"]')).toHaveCount(0);
      const leftLabel = page.locator('text[data-event-key="event:9003"]');
      await expect(leftLabel).toBeVisible();
      const leftBox = (await leftLabel.boundingBox())!;
      const dotBox = (await page.locator('[data-key="event:9003"] [data-slot=event-mark]').boundingBox())!;
      expect(leftBox.x + leftBox.width).toBeLessThan(dotBox.x);
      const legend = page.locator("[data-slot=timeline-legend]");
      await expect(legend).toContainText(locale === "zh-CN" ? "已验收" : "Accepted");
      await expect(legend).toContainText(locale === "zh-CN" ? "结论为失败" : "Failed conclusion");
      const hiddenName = page.locator('[data-key="event:9002"]');
      const status = page.locator('[data-slot=timeline-status]');
      const continuationName = locale === "zh-CN" ? "续接" : "Continuation";
      await hiddenName.hover();
      await expect(status).toContainText(continuationName);
      await legend.hover();
      await hiddenName.focus();
      await expect(status).toContainText(continuationName);
      await hiddenName.click();
      await legend.hover();
      await expect(status).toContainText(continuationName);
      await expect(hiddenName.locator('[data-slot=selected-item]')).toHaveCount(1);
      const accepted = page.locator("[data-slot=accept-mark]");
      expect(await accepted.count()).toBeGreaterThan(0);
      const acceptedLabels = await accepted.evaluateAll(marks => marks.map(mark => {
        const parent = mark.closest("[data-key]")!;
        const svg = (mark as SVGGraphicsElement).ownerSVGElement!;
        return !!svg.querySelector(`text[data-event-key="${parent.getAttribute("data-key")}"]`);
      }));
      expect(acceptedLabels).not.toContain(true);
      const geometry = await page.locator("text[data-event-key]").evaluateAll(labels => labels.map(label => {
        const key = label.getAttribute("data-event-key")!;
        const svg = (label as SVGTextElement).ownerSVGElement!;
        const marker = svg.querySelector<SVGCircleElement>(`[data-key="${key}"] [data-slot=event-mark]`);
        const box = label.getBoundingClientRect();
        const mark = marker?.getBoundingClientRect();
        const gap = mark ? Math.min(Math.abs(box.left - mark.right), Math.abs(mark.left - box.right)) : Infinity;
        const overlaps = Array.from(svg.querySelectorAll<SVGGraphicsElement>('[data-slot=timeline-break], [data-slot=execution-bar], [data-slot=routing-bar], [data-slot=acceptance-wait], [data-slot$="-wait"]'))
          .some(obstacle => {
            const area = obstacle.getBoundingClientRect();
            const centerY = (area.top + area.bottom) / 2;
            const top = area.height < 2 ? centerY - 1.5 : area.top;
            const bottom = area.height < 2 ? centerY + 1.5 : area.bottom;
            return box.left < area.right && box.right > area.left && box.top < bottom && box.bottom > top;
          });
        return { key, hasDot: !!marker, gap, overlaps };
      }));
      expect(geometry.length).toBeGreaterThan(0);
      for (const label of geometry) {
        expect(label.hasDot, label.key).toBe(true);
        expect(label.gap, label.key).toBeGreaterThanOrEqual(3);
        expect(label.gap, label.key).toBeLessThanOrEqual(12);
        expect(label.overlaps, label.key).toBe(false);
      }
      const selectionStyles = await page.locator('[data-slot=task-work] [data-slot=timeline-row-name]:visible, [data-slot=timeline-axis] text, [data-slot=timeline-track] text').evaluateAll(nodes =>
        nodes.map(node => ({ slot: node.getAttribute("data-slot"), userSelect: getComputedStyle(node).userSelect })));
      expect(selectionStyles.length).toBeGreaterThan(0);
      for (const style of selectionStyles) expect(style.userSelect, style.slot ?? "timeline text").toBe("none");
      await page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a1"]:visible').click();
      await expect(page.locator('[data-slot=detail-strip]')).toBeVisible();
      expect(await page.evaluate(() => window.getSelection()?.toString() ?? "")).toBe("");
      await page.locator('[data-slot=detail-header]').getByRole("button", { name: locale === "zh-CN" ? "收起详情" : "Close detail" }).click();
      if (width === 390) for (let n = 0; n < 2; n++) await page.locator('[data-slot=timeline-zoom] button').nth(1).click();
      await page.locator('[data-key="span:routing:att-router-a1"]').dblclick();
      expect(await page.evaluate(() => window.getSelection()?.toString() ?? "")).toBe("");

      await page.goto(`#tasks/${MACRO_B}`);
      const failed = page.locator("[data-slot=failed-conclusion-mark]");
      await expect(failed.first()).toBeVisible();
      const failedLabels = await failed.evaluateAll(marks => marks.map(mark => {
        const parent = mark.closest("[data-key]")!;
        const svg = (mark as SVGGraphicsElement).ownerSVGElement!;
        return !!svg.querySelector(`text[data-event-key="${parent.getAttribute("data-key")}"]`);
      }));
      expect(failedLabels).not.toContain(true);
      const continuation = selectedRow(page, "preview-run-b1").locator(`[data-key^="event:"][aria-label*="${locale === "zh-CN" ? "续接" : "Continuation"}"]`);
      await expect(continuation).toBeVisible();
      await continuation.scrollIntoViewIfNeeded();
      if (locale === "zh-CN") await page.screenshot({ path: `${shots}/continuation-row-${theme}-${width}.png` });
    });
  });
}
