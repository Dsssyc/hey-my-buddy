import { expect, type Locator, type Page } from "@playwright/test";
import { open, settle, test } from "./preview";

const macro = "macro-22222222-2222-4222-8222-222222222222";
const detail = "#tasks/macro-11111111-1111-4111-8111-111111111111/preview-run-a3";

async function expectNowRuler(scope: Locator, stack: Locator) {
  const line = scope.locator('[data-slot=timeline-now]');
  await expect(line).toHaveCount(1);
  await expect(scope.locator('[data-slot=timeline-track] [data-slot=timeline-now]')).toHaveCount(0);
  await expect(line).toHaveCSS("pointer-events", "none");
  await expect(line).toHaveCSS("border-left-width", "2px");
  await expect(line).toHaveCSS("opacity", "0.8");
  const [ruler, rows, axis] = await Promise.all([
    line.boundingBox(), stack.boundingBox(), scope.locator('[data-slot=axis-baseline]').evaluate(node => node.getBoundingClientRect().right),
  ]);
  expect(ruler!.y).toBeCloseTo(rows!.y, 1);
  expect(ruler!.y + ruler!.height).toBeCloseTo(rows!.y + rows!.height, 1);
  expect(ruler!.x + ruler!.width / 2).toBeCloseTo(axis, 1);
  // The existing axis hides Now when the zoom-fit button occupies its text.
  // When present its right edge stays on the same instant, including offscreen.
  const label = scope.locator('[data-slot=axis-label].fill-live');
  if (await label.count()) {
    const box = (await label.boundingBox())!;
    expect(ruler!.x + ruler!.width / 2).toBeCloseTo(box.x + box.width, 0);
  }
  // Temporarily include the line in hit testing to inspect actual browser paint
  // order at every visible row border and execution intersection; restore before
  // testing the real pointer behaviour. This catches a higher row stacking layer.
  const samples = await line.evaluate(node => {
    const line = node as HTMLElement;
    const bounds = line.getBoundingClientRect();
    const x = bounds.left + .5;
    const viewport = line.closest('[data-radix-scroll-area-viewport]')!.getBoundingClientRect();
    const tracks = [...line.parentElement!.querySelectorAll('[data-slot=timeline-track], [data-slot=timeline-row], [data-slot=timeline-lane-lead]')];
    const bars = [...line.parentElement!.querySelectorAll('[data-slot=execution-bar]')];
    const ys = [bounds.top + .5, bounds.bottom - .5,
      ...tracks.map(track => track.getBoundingClientRect().top + .5),
      ...bars.flatMap(bar => {
        const box = bar.getBoundingClientRect();
        return x >= box.left && x <= box.right ? [box.top + box.height / 2] : [];
      })].filter(y => y >= Math.max(0, viewport.top) && y < Math.min(innerHeight, viewport.bottom));
    const saved = line.style.pointerEvents;
    line.style.pointerEvents = "auto";
    try { return ys.map(y => ({ y, above: document.elementFromPoint(x, y) === line })); }
    finally { line.style.pointerEvents = saved; }
  });
  expect(samples.length).toBeGreaterThan(1);
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
    test.use({ locale: "en-US", viewport: { width, height: 1000 }, reducedMotion: "reduce" });
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
      for (const zoomed of [false, true]) {
        if (zoomed) {
          await page.locator('[data-slot=zoom-ruler]').press('+');
          await page.locator('[data-slot=timeline-tracks]').evaluate(node => { node.scrollLeft = node.scrollWidth; });
          await expect.poll(() => page.locator('[data-slot=timeline-tracks]').evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
        }
        await settle(page);
        await expectNowRuler(scope, stack);
      }
      await clickThroughNow(page, scope);
      await expect(page.locator('[data-slot=task-work]')).toHaveAttribute('data-mode', 'detail');

      await page.goto(detail);
      const strip = page.locator('[data-slot=detail-strip]');
      const stripStack = strip.locator('[data-slot=timeline-track]').locator('xpath=..');
      for (const zoomed of [false, true]) {
        if (zoomed) {
          await strip.locator('[data-slot=zoom-ruler]').press('+');
          await page.locator('[data-slot=detail-strip-scroll]').evaluate(node => { node.scrollLeft = node.scrollWidth; });
          await expect.poll(() => page.locator('[data-slot=detail-strip-scroll]').evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
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
    });
  });
}
