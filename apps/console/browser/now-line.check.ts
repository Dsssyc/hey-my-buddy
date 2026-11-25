import { expect, type Locator, type Page } from "@playwright/test";
import { expandDetailTimeline, open, settle, test } from "./preview";

const macro = "macro-22222222-2222-4222-8222-222222222222";
const detail = "#tasks/macro-11111111-1111-4111-8111-111111111111/preview-run-a3";

async function expectNowRuler(scope: Locator, stack: Locator) {
  const line = scope.locator('[data-slot=timeline-now]');
  const axisLine = scope.locator('[data-slot=timeline-now-axis]');
  await expect(line).toHaveCount(1);
  await expect(axisLine).toHaveCount(1);
  await expect(scope.locator('[data-slot=timeline-track] [data-slot=timeline-now]')).toHaveCount(0);
  await expect(line).toHaveCSS("pointer-events", "none");
  await expect(line).toHaveCSS("border-left-width", "2px");
  await expect(line).toHaveCSS("opacity", "0.8");
  await expect(axisLine).toHaveCSS("pointer-events", "none");
  await expect(axisLine).toHaveCSS("stroke-width", "2px");
  await expect(axisLine).toHaveCSS("opacity", "0.8");
  // Chromium's protocol box adds a square stroke outset even for butt caps.
  // DOM geometry gives the rendered segment's endpoints and centre instead.
  const [ruler, rows, axis, upper] = await Promise.all([
    line.boundingBox(), stack.boundingBox(), scope.locator('[data-slot=timeline-axis]').boundingBox(),
    axisLine.evaluate(node => { const { x, y, height } = node.getBoundingClientRect(); return { x, y, height }; }),
  ]);
  const close = (actual: number, expected: number) => expect(Math.abs(actual - expected)).toBeLessThanOrEqual(.5);
  close(ruler!.y, rows!.y);
  close(ruler!.y + ruler!.height, rows!.y + rows!.height);
  close(upper!.y, axis!.y);
  close(upper!.y + upper!.height, axis!.y + axis!.height);
  close(ruler!.x + ruler!.width / 2, upper!.x);
  // Scrolling moves rows behind the sticky axis, so the segments overlap in
  // layout coordinates. At the start they meet exactly, without a border gap.
  expect(ruler!.y - (upper!.y + upper!.height)).toBeLessThanOrEqual(.5);
  expect(ruler!.y + ruler!.height).toBeGreaterThan(upper!.y + upper!.height);
  // The existing axis hides Now when the zoom-fit button occupies its text.
  // When present its right edge stays on the same instant, including offscreen.
  const label = scope.locator('[data-slot=axis-label].fill-live');
  if (await label.count()) {
    const box = (await label.boundingBox())!;
    close(upper!.x, box.x + box.width);
    expect(await label.evaluate(node => !!(node.compareDocumentPosition(node.parentElement!.querySelector('[data-slot=timeline-now-axis]')!) & Node.DOCUMENT_POSITION_PRECEDING)), "Now lettering paints after the axis ruler").toBe(true);
  }
  const axisSamples = await axisLine.evaluate(node => {
    const line = node as SVGLineElement;
    const bounds = line.getBoundingClientRect();
    const viewport = line.closest('[data-radix-scroll-area-viewport]')!.getBoundingClientRect();
    if (bounds.x <= viewport.left || bounds.x >= viewport.right) return [];
    const saved = line.style.pointerEvents;
    // The transparent fit button owns a larger hit area than its icon. Leave
    // its paint intact but exclude that hit area when inspecting the ticks.
    const fit = line.closest('[data-slot=zoom-ruler]')!.querySelector<HTMLElement>('[data-slot=zoom-fit]');
    const fitEvents = fit?.style.pointerEvents;
    if (fit) fit.style.pointerEvents = 'none';
    line.style.pointerEvents = 'stroke';
    try {
      // Hit testing includes the next SVG within a pixel of its boundary.
      // Endpoint continuity is checked by the exact geometry above.
      return [1, 24.5, 30, bounds.height - 1].map(offset => {
        const hit = document.elementFromPoint(bounds.x, bounds.y + offset);
        return { offset, above: hit === line, hit: hit?.outerHTML.slice(0, 180) };
      });
    } finally {
      line.style.pointerEvents = saved;
      if (fit) fit.style.pointerEvents = fitEvents!;
    }
  });
  expect(axisSamples.filter(sample => !sample.above), "axis ruler paints above ticks and baseline").toEqual([]);
  // Temporarily include the line in hit testing to inspect actual browser paint
  // order at every visible row border and execution intersection; restore before
  // testing the real pointer behaviour. This catches a higher row stacking layer.
  const samples = await line.evaluate(node => {
    const line = node as HTMLElement;
    const bounds = line.getBoundingClientRect();
    const x = bounds.left + .5;
    const viewportElement = line.closest('[data-radix-scroll-area-viewport]')!;
    const viewport = viewportElement.getBoundingClientRect();
    if (x <= viewport.left || x >= viewport.right) return [];
    const axisBottom = viewportElement.querySelector('[data-slot=timeline-axis]')!.getBoundingClientRect().bottom;
    // The overlay scrollbar intentionally paints over the ruler. Only sample
    // rows in the content area above it, independent of header wrapping.
    const scrollbar = viewportElement.parentElement!.querySelector('[data-slot=scroll-area-scrollbar][data-orientation=horizontal]');
    const contentBottom = scrollbar ? Math.min(viewport.bottom, scrollbar.getBoundingClientRect().top) : viewport.bottom;
    const tracks = [...line.parentElement!.querySelectorAll('[data-slot=timeline-track], [data-slot=timeline-row], [data-slot=timeline-lane-lead]')];
    const bars = [...line.parentElement!.querySelectorAll('[data-slot=execution-bar]')];
    const ys = [Math.max(bounds.top, axisBottom) + .5, bounds.bottom - .5,
      ...tracks.map(track => track.getBoundingClientRect().top + .5),
      ...bars.flatMap(bar => {
        const box = bar.getBoundingClientRect();
        return x >= box.left && x <= box.right ? [box.top + box.height / 2] : [];
      })].filter(y => y >= Math.max(0, viewport.top, axisBottom) && y < Math.min(innerHeight, contentBottom));
    const saved = line.style.pointerEvents;
    line.style.pointerEvents = "auto";
    try { return ys.map(y => ({ y, above: document.elementFromPoint(x, y) === line })); }
    finally { line.style.pointerEvents = saved; }
  });
  if (axisSamples.length) expect(samples.length).toBeGreaterThan(1);
  expect(samples.filter(sample => !sample.above), "the continuous ruler paints above bars and row separators").toEqual([]);
}

async function clickThroughNow(page: Page, scope: Locator) {
  const live = scope.locator('[data-slot=execution-bar][data-outcome=running]').last();
  await live.scrollIntoViewIfNeeded();
  const line = (await scope.locator('[data-slot=timeline-now]').boundingBox())!;
  const bar = (await live.boundingBox())!;
  const point = { x: line.x + .5, y: bar.y + bar.height / 2 };
  const key = await scope.locator('[data-slot=timeline-now]').evaluate((node, point) => {
    const line = node as HTMLElement;
    line.style.visibility = 'hidden';
    try { return document.elementFromPoint(point.x, point.y)?.closest('[data-key]')?.getAttribute('data-key'); }
    finally { line.style.visibility = ''; }
  }, point);
  expect(key).toBeTruthy();
  const fragment = scope.locator(`[data-key="${key}"]`);
  expect(await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest('[data-key]')?.getAttribute('data-key'), point)).toBe(key);
  await page.mouse.move(point.x, point.y);
  await expect(fragment.locator('[data-slot=selected-item]')).toBeAttached();
  await fragment.focus();
  await expect(fragment).toBeFocused();
  await page.mouse.click(point.x, point.y);
}

for (const theme of ["dark", "light"]) for (const width of [1440, 390]) {
  test.describe(`continuous Now ruler ${theme} ${width}`, () => {
    test.use({ locale: "en-US", viewport: { width, height: 700 }, reducedMotion: "reduce" });
    test("stays above Lead lanes, bars and separators through zoom and scrolling, without taking hits", async ({ page }) => {
      await page.clock.setFixedTime(new Date("2026-09-27T10:00:00.000Z"));
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/${macro}`);
      const scope = page.locator('[data-slot=timeline-canvas]');
      const stack = scope.locator('[data-slot=timeline-rows]');
      await expect(page.locator('[data-slot=timeline-lane-lead]')).not.toHaveCount(0);
      await page.evaluate(() => document.fonts.ready);
      await settle(page);
      await expect(scope.locator('[data-slot=axis-label].fill-live')).toHaveCount(1);
      const viewport = page.locator('[data-slot=timeline-tracks]');
      const axisTop = (await scope.locator('[data-slot=timeline-axis]').boundingBox())!.y;
      for (const zoomed of [false, true]) {
        if (zoomed) {
          await page.locator('[data-slot=zoom-ruler]').press('+');
          await viewport.evaluate(node => { node.scrollLeft = node.scrollWidth; });
          await expect.poll(() => viewport.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
        }
        for (const position of [0, .5, 1]) {
          await viewport.evaluate((node, position) => { node.scrollTop = (node.scrollHeight - node.clientHeight) * position; }, position);
          if (position > 0) await expect.poll(() => viewport.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
          await settle(page);
          expect(Math.abs((await scope.locator('[data-slot=timeline-axis]').boundingBox())!.y - axisTop)).toBeLessThanOrEqual(.5);
          await expectNowRuler(scope, stack);
        }
        if (zoomed) for (const position of [0, .5, 1]) {
          await viewport.evaluate((node, position) => { node.scrollLeft = (node.scrollWidth - node.clientWidth) * position; }, position);
          await settle(page);
          await expectNowRuler(scope, stack);
        }
      }
      await clickThroughNow(page, scope);
      await expect(page.locator('[data-slot=task-work]')).toHaveAttribute('data-mode', 'detail');

      await page.goto(detail);
      await expandDetailTimeline(page);
      const strip = page.locator('[data-slot=detail-strip]');
      const stripStack = strip.locator('[data-slot=timeline-track]').locator('xpath=..');
      for (const zoomed of [false, true]) {
        if (zoomed) {
          await strip.locator('[data-slot=zoom-ruler]').press('+');
          const viewport = page.locator('[data-slot=detail-strip-scroll]');
          for (const position of [0, .5, 1]) {
            await viewport.evaluate((node, position) => { node.scrollLeft = (node.scrollWidth - node.clientWidth) * position; }, position);
            await settle(page);
            await expectNowRuler(strip, stripStack);
          }
          await expect.poll(() => viewport.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
        }
        await settle(page);
        await expectNowRuler(strip, stripStack);
      }
      await clickThroughNow(page, strip);
      await expect(strip.locator('[data-key="span:execution:att-a3-1"]')).toHaveAttribute('aria-pressed', 'true');
      await expect(strip.locator('[data-slot=timeline-now]')).toHaveJSProperty('tabIndex', -1);
      expect(await strip.locator('[data-slot=timeline-now]').evaluate(node => node.getAnimations().length)).toBe(0);
      await page.goto(detail.replace('preview-run-a3', 'preview-run-a1'));
      await expect(strip.locator('[data-slot=timeline-track]')).toHaveCount(1);
      await expect(strip.locator('[data-slot=timeline-now]')).toHaveCount(0);
      await expect(strip.locator('[data-slot=timeline-now-axis]')).toHaveCount(0);
    });
  });
}
