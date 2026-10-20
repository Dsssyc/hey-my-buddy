import { expect, type Locator, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, test } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";
const captures = fileURLToPath(new URL("../../../tmp/readability-r8", import.meta.url));
const ruler = (page: Page) => page.locator('[data-slot=zoom-ruler]:visible');
async function shot(page: Page, name: string) { await mkdir(captures, { recursive: true }); await page.screenshot({ path: `${captures}/${name}.png` }); }
async function point(node: Locator, portion = .6) {
  return node.evaluate((node, portion) => {
    const box = node.getBoundingClientRect();
    const frame = node.closest('[data-slot=timeline-tracks], [data-slot=detail-strip-scroll]')?.getBoundingClientRect();
    const left = Math.max(box.left, frame?.left ?? 0), right = Math.min(box.right, frame?.right ?? innerWidth);
    const top = Math.max(box.top, frame?.top ?? 0), bottom = Math.min(box.bottom, frame?.bottom ?? innerHeight);
    return { x: left + (right - left) * portion, y: top + (bottom - top) / 2 };
  }, portion);
}
async function moveTo(page: Page, node: Locator, portion = .6) { const p = await point(node, portion); await page.mouse.move(p.x, p.y); return p; }
const ppm = (page: Page) => page.locator('[data-slot=timeline-canvas]').getAttribute("data-ppm").then(Number);

for (const theme of ["dark", "light"]) for (const width of [1440, 390]) test.describe(`ruler workspace ${theme} ${width}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 900 }, reducedMotion: "reduce" });
  test("zooms at the ruler, pans with Shift and horizontal input, and exposes an overlay fit control", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, `#tasks/${A}`);
    const zoomRuler = ruler(page), view = page.locator('[data-slot=timeline-tracks]');
    await expect(zoomRuler).toBeVisible();
    const initial = await ppm(page);
    await expect(page.locator('[data-slot=zoom-fit]')).toHaveCount(0);
    const ground = await zoomRuler.evaluate(node => getComputedStyle(node).backgroundColor);
    const axis = page.locator('[data-slot=timeline-axis]');
    await moveTo(page, axis, .4);
    await expect(zoomRuler).toHaveCSS("background-color", ground);
    await expect(page.getByRole("tooltip")).toHaveText("滚轮缩放 · Shift+滚轮平移 · 双击适应宽度 · 键盘 + − 0");
    await page.mouse.wheel(0, -350);
    await expect.poll(() => ppm(page)).toBeGreaterThan(initial);
    const zoomed = await ppm(page);
    const fit = page.locator('[data-slot=zoom-fit]');
    await expect(fit).toBeVisible();
    const frame = (await view.boundingBox())!, fitBox = (await fit.boundingBox())!;
    expect(fitBox.x + fitBox.width).toBeLessThanOrEqual(frame.x + frame.width);
    expect(fitBox.x).toBeGreaterThan(frame.x + frame.width - 50);
    await view.evaluate(node => { node.scrollLeft = 0; });
    await moveTo(page, page.locator('[data-slot=timeline-ruler]'), .65);
    // Supply the horizontal delta produced by a platform's Shift-wheel mapping.
    await page.keyboard.down("Shift"); await page.mouse.wheel(180, 0); await page.keyboard.up("Shift");
    await expect.poll(() => view.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
    expect(await ppm(page)).toBe(zoomed);
    expect(await zoomRuler.evaluate(node => {
      const event = new WheelEvent("wheel", { bubbles: true, cancelable: true, shiftKey: true, deltaY: 120 });
      node.dispatchEvent(event); return event.defaultPrevented;
    })).toBe(false);
    await view.evaluate(node => { node.scrollLeft = 0; });
    await page.mouse.wheel(180, 0);
    await expect.poll(() => view.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
    expect(await ppm(page)).toBe(zoomed);
    await fit.click();
    await expect.poll(() => ppm(page)).toBeCloseTo(initial, 5);
    await expect(page.locator('[data-slot=zoom-fit]')).toHaveCount(0);
    await moveTo(page, axis, .4); await page.mouse.wheel(0, -350);
    await expect.poll(() => ppm(page)).toBeGreaterThan(initial);
    const doubleClick = await moveTo(page, axis, .4);
    await page.mouse.dblclick(doubleClick.x, doubleClick.y);
    await expect.poll(() => ppm(page)).toBeCloseTo(initial, 5);
    await expect(page.locator('[data-slot=zoom-fit]')).toHaveCount(0);
    await shot(page, `timeline-${theme}-${width}`);

    // Control-wheel is the browser's trackpad-pinch form, including over rows.
    await moveTo(page, page.locator('[data-slot=timeline-track]').first());
    const pageWidth = await page.evaluate(() => window.innerWidth);
    await page.keyboard.down("Control"); await page.mouse.wheel(0, -180); await page.keyboard.up("Control");
    await expect.poll(() => ppm(page)).toBeGreaterThan(initial);
    expect(await page.evaluate(() => window.innerWidth)).toBe(pageWidth);
    expect(await page.evaluate(() => window.visualViewport!.scale)).toBe(1);
    await ruler(page).press("0");
    await page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a1"]').click();
    const strip = page.locator('[data-slot=detail-strip]'), detailView = strip.locator('[data-slot=detail-strip-scroll]');
    await expect(strip.locator(':scope > [data-slot=scroll-area]')).toBeVisible();
    expect(await strip.locator(':scope > [data-slot=timeline-zoom]').count()).toBe(0);
    const track = strip.locator('[data-slot=timeline-track]');
    const beforeWidth = Number(await track.getAttribute("width"));
    await moveTo(page, track, .5); await page.mouse.wheel(0, -350);
    await expect.poll(async () => Number(await track.getAttribute("width"))).toBeGreaterThan(beforeWidth);
    await detailView.evaluate(node => { node.scrollLeft = 0; });
    await moveTo(page, ruler(page));
    await page.mouse.wheel(150, 0);
    await expect.poll(() => detailView.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
    const afterPanWidth = Number(await track.getAttribute("width"));
    await page.keyboard.down("Shift"); await page.mouse.wheel(0, 80); await page.keyboard.up("Shift");
    expect(Number(await track.getAttribute("width"))).toBe(afterPanWidth);
    await strip.locator('[data-slot=zoom-fit]').click();
    await expect.poll(async () => Number(await track.getAttribute("width"))).toBe(beforeWidth);
    await expect(strip.locator('[data-slot=zoom-fit]')).toHaveCount(0);
    await moveTo(page, strip.locator('[data-slot=step-navigation]'));
    await page.mouse.wheel(0, -180);
    await expect.poll(async () => Number(await track.getAttribute("width"))).toBeGreaterThan(beforeWidth);
    await ruler(page).press("0");
    await expect.poll(async () => Number(await track.getAttribute("width"))).toBe(beforeWidth);
    await shot(page, `detail-${theme}-${width}`);
    if (width === 1440) {
      await page.locator('[data-slot=task-names] [data-slot=timeline-row-name]').first().hover();
      const hint = page.locator('[data-slot=tooltip-content]');
      await expect(hint).toBeVisible();
      await expect(hint).toHaveCSS("text-align", "left");
      await shot(page, `name-hint-${theme}`);
    }
  });
});

test.describe("scroll continuity", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 560 }, reducedMotion: "reduce" });
  test("keeps the clicked row and legend control in place through open and close", async ({ page }) => {
    await open(page, `#tasks/${B}`);
    const view = page.locator('[data-slot=timeline-tracks]');
    const legend = page.locator('[data-slot=legend-trigger]');
    const legendHandle = (await legend.elementHandle())!;
    const legendY = (await legend.boundingBox())!.y;
    const top = (await view.boundingBox())!.y;
    await view.evaluate(node => { node.scrollTop = 140; });
    await expect.poll(() => view.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
    const selected = await view.locator('[data-slot=timeline-row-name]').evaluateAll((nodes, top) => {
      const node = nodes.find(node => { const box = node.getBoundingClientRect(); return box.y > top + 70 && box.y < top + 160; })!;
      return { runId: node.getAttribute("data-run-id"), y: node.getBoundingClientRect().y };
    }, top);
    await view.locator(`[data-run-id="${selected.runId}"][data-slot=timeline-row-name]`).click();
    const names = page.locator('[data-slot=task-names]');
    const row = names.locator(`[data-run-id="${selected.runId}"][data-slot=timeline-row-name]`);
    await expect(names).toBeVisible();
    await expect.poll(async () => Math.abs((await row.boundingBox())!.y - selected.y)).toBeLessThanOrEqual(1);
    expect(await legendHandle.evaluate(node => node.isConnected)).toBe(true);
    expect((await legend.boundingBox())!.y).toBe(legendY);
    const detailY = (await row.boundingBox())!.y;
    await page.keyboard.press("Escape");
    const returned = view.locator(`[data-run-id="${selected.runId}"][data-slot=timeline-row-name]`);
    await expect(returned).toBeVisible();
    expect(Math.abs((await returned.boundingBox())!.y - detailY)).toBeLessThanOrEqual(1);
    expect((await legend.boundingBox())!.y).toBe(legendY);
    expect(await legendHandle.evaluate(node => node.isConnected)).toBe(true);
    const before = await ppm(page);
    await moveTo(page, view, .8); await page.mouse.wheel(0, 80);
    await expect.poll(() => view.evaluate(node => node.scrollTop)).toBeGreaterThan(140);
    expect(await ppm(page)).toBe(before);
  });
});

test.describe("touch ruler", () => {
  test.use({ locale: "zh-CN", viewport: { width: 390, height: 900 }, hasTouch: true, reducedMotion: "reduce" });
  test("pinches through successive touch moves and double taps to fit", async ({ page }) => {
    await open(page, `#tasks/${A}`);
    const axis = page.locator('[data-slot=timeline-axis]');
    const p = await point(axis, .5), initial = await ppm(page);
    const session = await page.context().newCDPSession(page);
    const touch = (type: "touchStart" | "touchMove" | "touchEnd", spread: number) => session.send("Input.dispatchTouchEvent", { type, touchPoints: type === "touchEnd" ? [] : [{ x: p.x - spread, y: p.y, id: 1 }, { x: p.x + spread, y: p.y, id: 2 }] });
    await touch("touchStart", 20); await touch("touchMove", 40);
    await expect.poll(() => ppm(page)).toBeGreaterThan(initial);
    const once = await ppm(page);
    await touch("touchMove", 70);
    await expect.poll(() => ppm(page)).toBeGreaterThan(once);
    await touch("touchEnd", 0);
    for (let n = 0; n < 2; n++) {
      await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: p.x, y: p.y, id: 1 }] });
      await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    }
    await expect.poll(() => ppm(page)).toBeCloseTo(initial, 5);
    await expect(page.locator('[data-slot=zoom-fit]')).toHaveCount(0);
    await session.detach();
  });
});

test("draws every waiting diamond completely above every execution bar", async ({ page }) => {
  await open(page, `#tasks/${B}`);
  const diamonds = page.locator('[data-slot=waiting-diamond]');
  await expect(diamonds.first()).toBeVisible();
  const covered = await diamonds.evaluateAll(nodes => nodes.flatMap(node => {
    const glyph = node as SVGGeometryElement, box = glyph.getBBox(), matrix = glyph.getScreenCTM()!;
    const obscured = [-.8, 0, .8].some(fraction => {
      const point = new DOMPoint(box.x + box.width / 2 + fraction * box.width / 2, box.y + box.height / 2).matrixTransform(matrix);
      const svg = glyph.closest('svg')!;
      return [...svg.querySelectorAll<SVGGeometryElement>('[data-slot=execution-bar]')].some(bar => {
        if (!(glyph.compareDocumentPosition(bar) & Node.DOCUMENT_POSITION_FOLLOWING)) return false;
        const local = point.matrixTransform(bar.getScreenCTM()!.inverse());
        return bar.isPointInFill(local);
      });
    });
    return obscured ? [node.outerHTML] : [];
  }));
  expect(covered).toEqual([]);
});
