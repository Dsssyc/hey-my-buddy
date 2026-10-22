import { expect } from "@playwright/test";
import { open, settle, test } from "./preview";
import type { MacroTaskTimeline } from "../src/macro-task-types";

const macro = "macro-22222222-2222-4222-8222-222222222222";
const observedAt = "2026-09-27T10:00:00.000Z";

for (const mode of ["overlay", "classic"] as const) for (const theme of ["dark", "light"] as const) {
  test.describe(`timeline scrollbar gutter ${mode} ${theme}`, () => {
    test("keeps the last conclusion's outer stroke, ruler and labels clear of the vertical scrollbar", async ({ playwright, baseURL }, testInfo) => {
      const browser = await playwright.chromium.launch({
        // Playwright hides native scrollbars by default. Force real overlay or
        // classic metrics and verify them below, independently of Radix's bars.
        ignoreDefaultArgs: ["--hide-scrollbars"],
        args: [mode === "overlay" ? "--blink-settings=forceAndroidOverlayScrollbar=true" : "--disable-features=OverlayScrollbar"],
      });
      try {
        const context = await browser.newContext({ baseURL, locale: "zh-CN", viewport: { width: 1440, height: 650 }, colorScheme: theme, reducedMotion: "reduce" });
        const page = await context.newPage();
        const errors: string[] = [];
        page.on("pageerror", error => errors.push(String(error)));
        page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
        const probe = await context.newPage();
        await probe.setContent('<div style="width:100px;height:100px;overflow:scroll"><div style="width:200px;height:200px"></div></div>');
        const nativeGutter = await probe.locator("body > div").evaluate(node => (node as HTMLElement).offsetWidth - node.clientWidth);
        await probe.close();
        if (mode === "overlay") expect(nativeGutter, "native overlay scrollbars take no layout width").toBe(0);
        else expect(nativeGutter, "native classic scrollbars take layout width").toBeGreaterThan(0);
        testInfo.annotations.push({ type: "native-scrollbar-gutter", description: `${mode}: ${nativeGutter}px` });

        await page.clock.setFixedTime(new Date(observedAt));
        await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
        await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
          const response = await route.fetch();
          const timeline = await response.json() as MacroTaskTimeline;
          // Keep a real accepted preview micro task exactly at the domain end.
          timeline.rows.find(row => row.runId === "preview-run-b9")!.acceptedAt = observedAt;
          timeline.events.find(event => event.runId === "preview-run-b9" && event.kind === "accept")!.at = observedAt;
          await route.fulfill({ response, headers: { ...response.headers(), date: new Date(observedAt).toUTCString() }, json: timeline });
        });
        await open(page, `#tasks/${macro}`);
        const viewport = page.locator("[data-slot=timeline-tracks]");
        const area = viewport.locator("xpath=..");
        const scrollbar = area.locator(":scope > [data-slot=scroll-area-scrollbar][data-orientation=vertical]");
        const marker = page.locator('[data-slot=timeline-row][data-run-id=preview-run-b9] [data-slot=accept-mark] circle');
        await expect(marker).toBeVisible();
        expect(await viewport.evaluate(node => node.scrollHeight > node.clientHeight), "the fixture actually scrolls vertically").toBe(true);

        for (const zoomed of [false, true]) await test.step(zoomed ? "zoomed and scrolled to the end" : "fitted to the viewport", async () => {
          if (zoomed) await page.locator("[data-slot=zoom-ruler]").press("+");
          await viewport.evaluate(node => { node.scrollLeft = node.scrollWidth; });
          await marker.scrollIntoViewIfNeeded();
          await scrollbar.hover();
          await expect(scrollbar).toHaveCSS("opacity", "1");
          await settle(page);
          const view = await viewport.evaluate(node => {
            const box = node.getBoundingClientRect();
            return { left: box.left + node.clientLeft, right: box.left + node.clientLeft + node.clientWidth,
              top: box.top + node.clientTop, bottom: box.top + node.clientTop + node.clientHeight };
          });
          const outer = await marker.evaluate(node => {
            const circle = node as SVGCircleElement;
            const box = circle.getBoundingClientRect(), matrix = circle.getScreenCTM()!;
            const halfStroke = parseFloat(getComputedStyle(circle).strokeWidth) / 2;
            const dx = halfStroke * Math.hypot(matrix.a, matrix.c), dy = halfStroke * Math.hypot(matrix.b, matrix.d);
            return { left: box.left - dx, right: box.right + dx, top: box.top - dy, bottom: box.bottom + dy };
          });
          const bar = (await scrollbar.boundingBox())!;
          expect(outer.left).toBeGreaterThanOrEqual(view.left);
          expect(outer.right, "the conclusion's full stroke is inside the visible viewport").toBeLessThanOrEqual(view.right + 0.1);
          expect(outer.top).toBeGreaterThanOrEqual(view.top);
          expect(outer.bottom).toBeLessThanOrEqual(view.bottom);
          expect(view.right, "the scroll layer reserves the rendered scrollbar's own width").toBeLessThanOrEqual(bar.x + 0.1);
          expect(outer.right, "the scrollbar cannot cover the conclusion").toBeLessThanOrEqual(bar.x + 0.1);
          const svgRight = await marker.evaluate(node => node.closest("svg")!.getBoundingClientRect().right);
          expect(outer.right, "the SVG does not clip the conclusion's outer stroke").toBeLessThanOrEqual(svgRight + 0.1);
          const axisRight = await page.locator("[data-slot=axis-baseline], [data-slot=timeline-axis] [data-slot=axis-label]")
            .evaluateAll(nodes => Math.max(...nodes.map(node => node.getBoundingClientRect().right)));
          expect(axisRight, "the ruler and its labels stay clear of the scrollbar").toBeLessThanOrEqual(Math.min(view.right, bar.x) + 0.1);
          const labelRight = await page.locator('[data-slot=timeline-row][data-run-id=preview-run-b9] [data-slot=track-label]')
            .evaluateAll(nodes => Math.max(...nodes.map(node => node.getBoundingClientRect().right)));
          expect(labelRight, "the final row's labels stay clear of the scrollbar").toBeLessThanOrEqual(Math.min(view.right, bar.x) + 0.1);
        });
        expect(errors, "errors reported by the page").toEqual([]);
      } finally { await browser.close(); }
    });
  });
}
