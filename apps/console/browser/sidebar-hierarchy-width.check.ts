import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect, type Page } from "@playwright/test";
import { open, settle, test } from "./preview";

const captures = fileURLToPath(new URL("../../../tmp/sidebar-hierarchy-width", import.meta.url));
const listSelector = '[data-slot="macro-list"]';
const handleSelector = '[data-slot="macro-sidebar-resize"]';

async function drag(page: Page, delta: number) {
  const handle = page.locator(handleSelector);
  const box = (await handle.boundingBox())!;
  await page.mouse.move(box.x, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + delta, box.y + box.height / 2, { steps: 12 });
  await page.mouse.up();
}

for (const scene of [
  { locale: "zh-CN", theme: "dark" },
  { locale: "zh-CN", theme: "light" },
  { locale: "en-US", theme: "dark" },
]) test.describe(`sidebar hierarchy ${scene.locale} ${scene.theme}`, () => {
  test.use({ locale: scene.locale, viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  test("keeps hierarchy and pixel bounds through drag, reload, keyboard and collapse", async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem("hey-my-buddy.console.theme", theme), scene.theme);
    await open(page, "#tasks");
    const list = page.locator(listSelector);
    const handle = page.getByRole("separator", { name: scene.locale === "zh-CN" ? "调整宏任务列表宽度" : "Resize macro task list" });
    const width = () => list.evaluate(node => node.getBoundingClientRect().width);
    await expect.poll(width).toBe(288);
    await expect(handle).toHaveAttribute("aria-orientation", "vertical");
    const metrics = await list.evaluate(node => {
      const groups = [...node.querySelectorAll<HTMLElement>('[data-slot="macro-group"]')];
      const entry = node.querySelector<HTMLElement>('[data-slot="macro-entry"]')!;
      const title = entry.querySelector<HTMLElement>('[data-slot="macro-entry-title"]')!;
      return { row: entry.getBoundingClientRect().height,
        gaps: groups.slice(1).map(group => getComputedStyle(group).marginTop),
        project: node.querySelector('[data-slot="macro-group-heading"]')!.getBoundingClientRect().height,
        more: node.querySelector('[data-slot="macro-group-more"]')!.getBoundingClientRect().height,
        arrow: node.querySelector('[data-slot="macro-group-more"] svg')!.getBoundingClientRect().width,
        color: getComputedStyle(title).color, ink2: getComputedStyle(title).getPropertyValue('--ink2').trim() };
    });
    expect(metrics.row).toBe(28); expect(metrics.project).toBe(32);
    expect(metrics.gaps.every(gap => gap === "20px")).toBe(true);
    expect(metrics.more).toBe(26); expect(metrics.arrow).toBe(12);
    const idle = list.locator('[data-slot="macro-entry"][aria-pressed="false"]').first();
    // Resolve each theme's CSS token through the browser's color parser.
    const expectedColor = async (token: string) => page.evaluate(token => {
      const probe = document.createElement("span"); probe.style.color = `var(${token})`;
      document.body.append(probe); const color = getComputedStyle(probe).color; probe.remove(); return color;
    }, token);
    await expect(idle).toHaveCSS("color", await expectedColor("--ink2"));
    await idle.hover(); await expect(idle).toHaveCSS("color", await expectedColor("--ink"));
    await idle.click(); await expect(idle).toHaveCSS("color", await expectedColor("--ink"));
    await page.mouse.move(1430, 10);
    await mkdir(captures, { recursive: true });
    const name = `${scene.locale}-1440-${scene.theme}`;
    await settle(page);
    await page.screenshot({ path: `${captures}/${name}-default.png` });
    await drag(page, 160);
    await expect.poll(width).toBeCloseTo(448, 0);
    const saved = await width();
    await page.screenshot({ path: `${captures}/${name}-wide.png` });
    await page.reload(); await expect.poll(width).toBeCloseTo(saved, 0);
    await handle.focus(); await page.keyboard.press("ArrowRight");
    await expect.poll(width).toBeGreaterThan(saved);
    await page.keyboard.press("ArrowLeft"); await expect.poll(width).toBeCloseTo(saved, 0);
    await drag(page, -1000); await expect.poll(width).toBe(288);
    await drag(page, 1200); await expect.poll(width).toBeCloseTo(720, 0);
    await page.setViewportSize({ width: 1000, height: 900 });
    await expect.poll(width).toBeLessThanOrEqual(500);
    await page.setViewportSize({ width: 1440, height: 900 });
    await drag(page, -200);
    const expandedWidth = await width();
    await page.getByRole("button", { name: scene.locale === "zh-CN" ? "收起宏任务列表" : "Collapse macro task list", exact: true }).click();
    await expect(list).toBeHidden(); await expect(handle).toBeHidden();
    await page.getByRole("button", { name: scene.locale === "zh-CN" ? "展开宏任务列表" : "Expand macro task list", exact: true }).click();
    await expect.poll(width).toBeCloseTo(expandedWidth, 0);
    const facts = page.locator('[data-slot="macro-overview-facts"]');
    await expect(facts.locator("dt")).not.toContainText([scene.locale === "zh-CN" ? "开始" : "Started"]);
    await facts.screenshot({ path: `${captures}/${name}-facts.png` });
    await facts.getByRole("button", { name: scene.locale === "zh-CN" ? "时间统计" : "Time statistics", exact: true }).click();
    await expect(page.locator('[data-slot="time-stats-instants"] dt')).toHaveText(scene.locale === "zh-CN" ? ["开始", "最后活动"] : ["Started", "Last activity"]);
  });
});

test("a narrow sidebar fills the viewport and exposes no resize separator", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page, "#tasks");
  const list = page.locator(listSelector);
  await expect(list).toBeVisible();
  expect((await list.boundingBox())!.width).toBe(390);
  await expect(page.locator(handleSelector)).toHaveCount(0);
  await mkdir(captures, { recursive: true });
  await page.screenshot({ path: `${captures}/390-dark.png` });
  await page.setViewportSize({ width: 760, height: 844 });
  await expect(page.locator(handleSelector)).toHaveCount(0);
  await page.setViewportSize({ width: 761, height: 844 });
  await expect(page.locator(handleSelector)).toBeVisible();
});
