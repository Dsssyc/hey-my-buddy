import { expect } from "@playwright/test";
import { open, settle, test } from "./preview";
import type { MacroTaskTimeline } from "../src/macro-task-types";

const macro = "macro-22222222-2222-4222-8222-222222222222";
const observedAt = "2026-09-27T10:00:00.000Z";

for (const width of [1440, 390]) for (const theme of ["light", "dark"]) {
  test.describe(`timeline right edge ${width}px ${theme}`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 700 }, reducedMotion: "reduce" });
    test("paints through the gutter while keeping end marks and hits clear", async ({ page }, testInfo) => {
      await page.clock.setFixedTime(new Date(observedAt));
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/${macro}`);
      await page.evaluate(() => document.fonts.ready);
      await settle(page);
      await page.screenshot({ path: testInfo.outputPath(`after-${theme}-${width}.png`) });
      const viewport = page.locator('[data-slot=timeline-tracks]');
      const area = viewport.locator('xpath=..');
      const geometry = await viewport.evaluate(node => {
        const right = node.parentElement!.getBoundingClientRect().right;
        const canvas = node.querySelector('[data-slot=timeline-canvas]')!;
        return { right, view: node.getBoundingClientRect().right, padding: parseFloat(getComputedStyle(node).paddingRight),
          scrollWidth: node.scrollWidth, clientWidth: node.clientWidth, canvas: canvas.getBoundingClientRect().right,
          ppm: canvas.getAttribute('data-ppm'), now: node.querySelector('[data-slot=timeline-now]')!.getBoundingClientRect().right,
          axis: Math.max(...[...node.querySelectorAll('[data-slot=axis-baseline], [data-slot=axis-edge]')].map(line => line.getBoundingClientRect().right)) };
      });
      await testInfo.attach('fitted-geometry', { body: JSON.stringify(geometry, null, 2), contentType: 'application/json' });
      expect(geometry.axis, 'the complete axis reaches the viewport edge').toBeCloseTo(geometry.right, 1);
      expect(geometry.padding).toBeGreaterThan(0);
      expect(geometry.scrollWidth, 'paint does not add horizontal scrolling when fitted').toBe(geometry.clientWidth);
      expect(geometry.canvas).toBeCloseTo(geometry.right, 1);
      expect(geometry.now, 'the now line keeps its outset and scrollbar clearance').toBeCloseTo(geometry.right - geometry.padding - 18, 1);
      const header = page.locator('[data-slot=timeline-row-filter]').locator('xpath=..');
      if (width === 390) {
        const paint = await header.evaluate(node => ({ right: node.getBoundingClientRect().right,
          background: getComputedStyle(node).backgroundColor,
          filter: getComputedStyle(node.firstElementChild!).backgroundColor }));
        expect(paint.right).toBeCloseTo(geometry.right, 1);
        expect(paint.background).toBe(paint.filter);
      }
      for (const row of [page.locator('[data-slot=timeline-lane-lead]').first(), page.locator('[data-slot=timeline-row]').first(), page.locator('[data-slot=timeline-canvas] > [data-slot=timeline-row]').first()]) {
        const track = row.locator('[data-slot=timeline-track]');
        await track.hover();
        await expect(track.locator('[data-slot=selected-row]')).toBeAttached();
        const paint = await row.evaluate(node => {
          const track = node.querySelector('[data-slot=timeline-track]')!;
          const edge = node.querySelector('[data-slot=timeline-row-edge]')!;
          return { right: node.getBoundingClientRect().right, edge: edge.getBoundingClientRect().right,
            wash: getComputedStyle(track.querySelector('[data-slot=selected-row]')!).fill,
            edgeWash: getComputedStyle(edge).backgroundColor, separator: getComputedStyle(edge).borderTopWidth };
        });
        expect(paint.right).toBeCloseTo(geometry.right, 1);
        expect(paint.edge).toBeCloseTo(geometry.right, 1);
        expect(paint.edgeWash).toBe(paint.wash);
        if (width === 390) {
          const namePaint = await row.locator('[data-slot=timeline-name-edge]').evaluate(node => ({
            right: node.getBoundingClientRect().right, wash: getComputedStyle(node).backgroundColor,
            separator: getComputedStyle(node).borderTopWidth }));
          expect(namePaint.right).toBeCloseTo(geometry.right, 1);
          expect(namePaint.wash).toBe(paint.wash);
          if (await row.getAttribute('data-slot') === 'timeline-row') expect(namePaint.separator).toBe('1px');
        }
        if (await row.getAttribute('data-slot') === 'timeline-row') expect(paint.separator).toBe('1px');
      }
      // Put a failed one-second execution exactly at the domain end, exercising
      // the cross and its paper outside the bar rather than an easy middle bar.
      await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
        const response = await route.fetch();
        const timeline = await response.json() as MacroTaskTimeline;
        const span = timeline.spans.find(span => span.spanId === 'execution:att-b9-2')!;
        Object.assign(span, { startAt: '2026-09-27T09:59:59.000Z', endAt: observedAt, resultStatus: 'failed', error: 'Synthetic end failure' });
        await route.fulfill({ response, json: timeline });
      });
      await page.reload();
      const row = page.locator('[data-slot=timeline-row][data-run-id=preview-run-b9]');
      const fragment = row.locator('[data-key="span:execution:att-b9-2"]');
      const cross = fragment.locator('[data-slot=execution-failed]');
      await expect(cross).toBeAttached();
      for (const zoomed of [false, true]) {
        if (zoomed) await page.locator('[data-slot=zoom-ruler]').press('+');
        await viewport.evaluate(node => { node.scrollLeft = node.scrollWidth; });
        await fragment.scrollIntoViewIfNeeded();
        const scrollbar = area.locator(':scope > [data-slot=scroll-area-scrollbar][data-orientation=vertical]');
        const barBox = (await scrollbar.boundingBox())!;
        await page.mouse.move(barBox.x + barBox.width / 2, barBox.y + barBox.height / 2);
        await expect(scrollbar).toHaveCSS('opacity', '1');
        await scrollbar.hover();
        await settle(page);
        const bounds = await fragment.evaluate(node => {
          const view = node.closest('[data-slot=timeline-tracks]')!;
          const bar = view.parentElement!.querySelector('[data-slot=scroll-area-scrollbar][data-orientation=vertical]')!.getBoundingClientRect();
          const svg = node.closest('svg')!.getBoundingClientRect();
          const paper = node.querySelector('[data-slot=execution-failed]')!.previousElementSibling as SVGGraphicsElement;
          const box = paper.getBoundingClientRect();
          const halfStroke = parseFloat(getComputedStyle(paper).strokeWidth) / 2;
          const hit = node.querySelector('[data-slot=span-hit]')!.getBoundingClientRect();
          return { paperRight: box.right + halfStroke, svgRight: svg.right, barLeft: bar.left, hitRight: hit.right,
            viewRight: view.getBoundingClientRect().right, axisRight: view.querySelector('[data-slot=axis-edge]')!.getBoundingClientRect().right };
        });
        expect(bounds.paperRight, 'the cross and its complete paper remain inside the SVG').toBeLessThanOrEqual(bounds.svgRight + 0.1);
        expect(bounds.svgRight, 'the scrollbar never overlaps the interactive time area').toBeLessThanOrEqual(bounds.barLeft + 0.1);
        expect(bounds.hitRight).toBeLessThanOrEqual(bounds.barLeft + 0.1);
        expect(Math.abs(bounds.axisRight - bounds.viewRight), 'the painted edge reaches the viewport after fractional scrolling').toBeLessThanOrEqual(0.5);
        await page.screenshot({ path: testInfo.outputPath(`end-mark-${theme}-${width}-${zoomed ? 'zoomed' : 'fitted'}.png`) });
        await fragment.hover();
        await expect(fragment.locator('[data-slot=selected-item]')).toBeAttached();
      }
      await fragment.click();
      await expect(page.locator('[data-slot=task-work]')).toHaveAttribute('data-mode', 'detail');
    });
  });
}
