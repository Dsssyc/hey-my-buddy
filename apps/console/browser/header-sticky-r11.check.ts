import { expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, settle, test } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";
const captures = fileURLToPath(new URL("../../../tmp/readability-r11", import.meta.url));
for (const theme of ["dark", "light"]) for (const width of [1440, 390]) test.describe(`header and sticky ${theme} ${width}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test("keeps the macro ID last in the facts row and every lane name fixed while panning", async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem("hey-my-buddy.console.theme", theme), theme);
    await open(page, `#tasks/${B}`);
    const header = page.locator('[data-slot=macro-overview-header]');
    const facts = header.locator('[data-slot=macro-overview-facts]');
    const id = facts.getByRole("button", { name: `复制标识 ${B}` });
    await expect(id).toBeVisible();
    await expect(facts.locator(':scope > div').last()).toContainText("macro-22222222…2222");
    expect((await id.boundingBox())!.y).toBeGreaterThan((await header.locator("h2").boundingBox())!.y);
    if (width === 1440) {
      const project = (await facts.locator(":scope > div").first().boundingBox())!;
      const identifier = (await id.boundingBox())!;
      expect(Math.abs(project.y + project.height / 2 - identifier.y - identifier.height / 2)).toBeLessThanOrEqual(1);
      await page.getByRole("button", { name: "收起宏任务列表", exact: true }).click();
      const toggle = header.getByRole("button", { name: "展开宏任务列表", exact: true });
      await expect(toggle).toBeVisible();
      await settle(page);
      expect(Math.abs((await toggle.boundingBox())!.x - (await facts.boundingBox())!.x)).toBeLessThanOrEqual(1);
      await toggle.click(); await settle(page);
    }
    const ruler = page.locator('[data-slot=zoom-ruler]');
    await ruler.press("+"); await ruler.press("+");
    const names = page.locator('[data-slot=timeline-row-name]');
    await expect(names.first()).toBeVisible();
    const positions = () => names.evaluateAll(nodes => nodes.map(node => ({ id: node.getAttribute("data-run-id"), x: node.getBoundingClientRect().x })));
    const before = await positions();
    const view = page.locator('[data-slot=timeline-tracks]');
    await view.evaluate(node => node.scrollLeft = 300);
    await expect.poll(() => view.evaluate(node => node.scrollLeft)).toBe(300);
    const after = await positions();
    expect(after.map(row => row.id)).toEqual(before.map(row => row.id));
    after.forEach((row, index) => expect(Math.abs(row.x - before[index]!.x), row.id!).toBeLessThanOrEqual(1));
    await mkdir(captures, { recursive: true });
    await page.mouse.move(0, 0); await page.screenshot({ path: `${captures}/timeline-${theme}-${width}.png` });
    await names.first().click();
    const detail = page.locator('[data-slot=detail-header]');
    await expect(detail).toBeVisible();
    await expect(detail.getByRole("button", { name: /复制标识/ })).toBeVisible();
    await page.screenshot({ path: `${captures}/detail-${theme}-${width}.png` });
  });
});

test("keeps the stationary pointer under the fit button through consecutive wheel zooms", async ({ page }) => {
  await open(page, `#tasks/${A}`);
  const ruler = page.locator('[data-slot=zoom-ruler]');
  await ruler.press("+");
  const fit = page.locator('[data-slot=zoom-fit]');
  const box = (await fit.boundingBox())!;
  const x = box.x + box.width / 2;
  await page.mouse.move(x, box.y + box.height / 2);
  for (let index = 0; index < 4; index++) {
    const canvas = page.locator('[data-slot=timeline-canvas]');
    const before = Number(await canvas.getAttribute("data-ppm"));
    await page.mouse.wheel(0, -120);
    await expect.poll(async () => Number(await canvas.getAttribute("data-ppm"))).toBeGreaterThan(before);
    await expect.poll(async () => Math.abs((await page.locator('[data-slot=timeline-pointer]').boundingBox())!.x - x)).toBeLessThanOrEqual(1);
  }
});
