import { expect } from "@playwright/test";
import { open, settle, test } from "./preview";

test.use({ locale: "zh-CN", viewport: { width: 1280, height: 480 }, reducedMotion: "reduce" });
test("shows both shared scrollbars while hovering or scrolling and hides them without moving the tracks", async ({ page }) => {
  await open(page, "#tasks/macro-22222222-2222-4222-8222-222222222222");
  const viewport = page.locator('[data-slot=timeline-tracks]');
  const area = viewport.locator("xpath=..");
  const ruler = page.locator('[data-slot=zoom-ruler]:visible');
  await ruler.press("+"); await ruler.press("+");
  await viewport.hover();
  const vertical = area.locator(':scope > [data-slot=scroll-area-scrollbar][data-orientation=vertical]');
  const horizontal = area.locator(':scope > [data-slot=scroll-area-scrollbar][data-orientation=horizontal]');
  await expect(vertical).toHaveCount(1); await expect(horizontal).toHaveCount(1);
  await expect(vertical).toHaveCSS("opacity", "1"); await expect(horizontal).toHaveCSS("opacity", "1");
  const name = page.locator('[data-slot=timeline-row-name]').first();
  const left = (await name.boundingBox())!.x;
  const frame = (await viewport.boundingBox())!;
  expect((await vertical.boundingBox())!.x + (await vertical.boundingBox())!.width).toBeCloseTo(frame.x + frame.width, 0);
  await page.mouse.move(0, 0);
  await expect(vertical).toHaveCSS("opacity", "0"); await expect(horizontal).toHaveCSS("opacity", "0");
  await viewport.evaluate(node => { node.scrollTop = 120; node.scrollLeft = 100; });
  await expect(vertical).toHaveCSS("opacity", "1"); await expect(horizontal).toHaveCSS("opacity", "1");
  await expect(vertical).toHaveCSS("opacity", "0"); await expect(horizontal).toHaveCSS("opacity", "0");
  await settle(page);
  expect((await viewport.boundingBox())!.width).toBe(frame.width);
  expect((await name.boundingBox())!.x).toBe(left);
});
