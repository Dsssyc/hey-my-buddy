import { expect } from "@playwright/test";
import { open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";

for (const width of [390, 1440]) test.describe(`track style ${width}px`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 900 }, reducedMotion: "reduce" });

  test("keeps failed execution identity and a visible red outline, without status words inside bars", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
    await open(page, `#tasks/${macro}`);
    const failed = page.locator('[data-slot=execution-bar][data-outcome=failed]').first();
    await expect(failed).toBeAttached();
    const drawing = await failed.evaluate(bar => {
      const style = getComputedStyle(bar);
      const fail = getComputedStyle(document.documentElement).getPropertyValue("--fail").trim();
      const glyph = bar.parentElement!.querySelector('[data-slot=execution-failed]');
      return { fill: style.fill, stroke: style.stroke, width: Number(style.strokeWidth.replace("px", "")), fail, glyph: !!glyph,
        barWidth: bar.getBoundingClientRect().width };
    });
    expect(drawing.fill).toMatch(/^rgb\(/);
    expect(drawing.stroke).toBe(await page.evaluate(value => {
      const probe = document.createElement("span"); probe.style.color = value; document.body.append(probe);
      const color = getComputedStyle(probe).color; probe.remove(); return color;
    }, drawing.fail));
    expect(drawing.width).toBeGreaterThanOrEqual(2.5);
    expect(drawing.barWidth).toBeGreaterThan(0);
    expect(drawing.glyph).toBe(true);
    const words = await page.locator('[data-slot=timeline-track]').evaluateAll(tracks => tracks.flatMap(track => {
      const bars = [...track.querySelectorAll('[data-slot=execution-bar]')].map(bar => bar.getBoundingClientRect());
      return [...track.querySelectorAll('[data-slot=track-label]')].filter(label => {
        const box = label.getBoundingClientRect();
        return bars.some(bar => box.top >= bar.top && box.bottom <= bar.bottom && box.left >= bar.left && box.right <= bar.right);
      }).map(label => label.textContent ?? "");
    }));
    expect(words.join(" ")).not.toMatch(/进行中|执行中|失败|可能卡住|无动静/);
    await expect(page.locator('[data-outcome=failed]').first().locator('xpath=..')).toHaveAttribute("aria-label", /失败/);
  });

  for (const detail of [false, true]) test(`keeps axis clocks clear of the fit button in ${detail ? "detail" : "macro"}`, async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
    await open(page, `#tasks/${macro}${detail ? "/preview-run-a1/7" : ""}`);
    const surface = page.locator(detail ? '[data-slot=detail-strip]' : '[data-slot=timeline-tracks]');
    const ruler = surface.locator('[data-slot=zoom-ruler]');
    await ruler.press("+"); await ruler.press("+");
    const fit = ruler.locator('[data-slot=zoom-fit]');
    await expect(fit).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    for (const fraction of [0, 0.5, 1]) {
      const viewport = detail ? surface.locator('[data-slot=detail-strip-scroll]') : surface;
      await viewport.evaluate((node, fraction) => { node.scrollLeft = (node.scrollWidth - node.clientWidth) * fraction; }, fraction);
      await expect.poll(async () => ruler.evaluate(node => {
        const fit = node.querySelector('[data-slot=zoom-fit]')!.getBoundingClientRect();
        return [...node.querySelectorAll<SVGTextElement>('[data-slot=timeline-axis] [data-slot=axis-label]')].filter(label => {
          const box = label.getBoundingClientRect();
          return box.left < fit.right && fit.left < box.right && box.top < fit.bottom && fit.top < box.bottom;
        }).map(label => label.textContent);
      }), `axis clocks overlap fit at scroll fraction ${fraction}`).toEqual([]);
    }
  });
});
