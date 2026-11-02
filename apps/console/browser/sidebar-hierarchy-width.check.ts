import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect, type Page } from "@playwright/test";
import { open, settle, test } from "./preview";

const captures = fileURLToPath(new URL("../../../tmp/sidebar-hierarchy-width", import.meta.url));
const listSelector = '[data-slot="macro-list"]';
const handleSelector = '[data-slot="macro-sidebar-resize"]';

async function drag(page: Page, delta: number) {
  const handle = page.locator(handleSelector);
  const box = (await handle.boundingBox())!;
  // Start beyond the 1px line to exercise the library's larger pointer target.
  const x = box.x + 4;
  await page.mouse.move(x, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(x + delta, box.y + box.height / 2, { steps: 12 });
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
    await expect(list.locator('[data-slot="macro-entry"]').first()).toBeVisible();
    const metrics = await list.evaluate(node => {
      const groups = [...node.querySelectorAll<HTMLElement>('[data-slot="macro-group"]')];
      const entry = node.querySelector<HTMLElement>('[data-slot="macro-entry"]')!;
      return { row: entry.getBoundingClientRect().height,
        gaps: groups.slice(1).map(group => getComputedStyle(group).marginTop),
        project: node.querySelector('[data-slot="macro-group-heading"]')!.getBoundingClientRect().height,
        more: node.querySelector('[data-slot="macro-group-more"]')!.getBoundingClientRect().height,
        arrow: node.querySelector('[data-slot="macro-group-more"] svg')!.getBoundingClientRect().width };
    });
    expect(metrics.row).toBe(28); expect(metrics.project).toBe(32);
    expect(metrics.gaps.every(gap => gap === "20px")).toBe(true);
    expect(metrics.more).toBe(26); expect(metrics.arrow).toBe(12);
    const idleId = await list.locator('[data-slot="macro-entry"][aria-pressed="false"]').first().locator("..").getAttribute("data-macro-task-id");
    const idle = list.locator(`[data-macro-task-id="${idleId}"] [data-slot="macro-entry"]`);
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
    await expect(facts.locator("dt").filter({ hasText: /^(开始|Started)$/ })).toHaveCount(0);
    await facts.screenshot({ path: `${captures}/${name}-facts.png` });
    await facts.getByRole("button", { name: scene.locale === "zh-CN" ? "时间统计" : "Time statistics", exact: true }).click();
    await expect(page.locator('[data-slot="time-stats-instants"] dt')).toHaveText(scene.locale === "zh-CN" ? ["开始", "最后活动"] : ["Started", "Last activity"]);
  });
});

test("a narrow sidebar fills the viewport and exposes no resize separator", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("hey-my-buddy.console.theme", "dark");
    localStorage.setItem("hey-my-buddy.language", "zh");
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page, "#tasks");
  const list = page.locator(listSelector);
  await expect(list.locator('[data-slot="macro-entry"]').first()).toBeVisible();
  expect((await list.boundingBox())!.width).toBe(390);
  await expect(page.locator(handleSelector)).toHaveCount(0);
  await mkdir(captures, { recursive: true });
  await page.screenshot({ path: `${captures}/390-dark.png` });
  await page.setViewportSize({ width: 760, height: 844 });
  await expect(page.locator(handleSelector)).toHaveCount(0);
  await page.setViewportSize({ width: 761, height: 844 });
  await expect(page.locator(handleSelector)).toBeVisible();
});

for (const locale of ["zh-CN", "en-US"]) test.describe(`288px filter counts ${locale}`, () => {
  test.use({ locale, viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  test("fits two-digit counts without fading names or padding the underline", async ({ page }) => {
    await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
      const response = await route.fetch(); const data = await response.json();
      data.totals = { all: 123456, active: 12, host: 10, review: 12 };
      await route.fulfill({ response, json: data });
    });
    await open(page, "#tasks");
    const list = page.locator(listSelector);
    await expect.poll(() => list.evaluate(node => node.getBoundingClientRect().width)).toBe(288);
    const tabs = list.locator('[data-slot="macro-filter-tabs"] [role="tab"]');
    await expect(tabs).toHaveText(locale === "zh-CN" ? ["全部", "进行中12", "等待 Host10", "等待验收12"] : ["All", "Running12", "Host10", "Acceptance12"]);
    const metrics = await tabs.evaluateAll(nodes => nodes.map(node => {
      const text = node.querySelector<HTMLElement>('[data-slot="macro-filter-name"]')!;
      const count = node.querySelector<HTMLElement>('[data-slot="macro-filter-count"]');
      const style = getComputedStyle(node);
      const after = getComputedStyle(node, "::after");
      return { name: text.textContent, textWidth: text.clientWidth, content: text.scrollWidth, mask: getComputedStyle(text).maskImage,
        count: count?.textContent, countWidth: count?.clientWidth, countContent: count?.scrollWidth,
        font: style.fontSize, left: style.paddingLeft, right: style.paddingRight,
        width: node.getBoundingClientRect().width, underline: parseFloat(after.width),
        nextGap: node.nextElementSibling ? node.nextElementSibling.getBoundingClientRect().left - node.getBoundingClientRect().right : null };
    }));
    for (const tab of metrics) {
      expect(tab.textWidth).toBeGreaterThanOrEqual(tab.content);
      expect(tab.mask).toBe("none");
      if (tab.count) expect(tab.countWidth!).toBeGreaterThanOrEqual(tab.countContent!);
      expect(tab.font).toBe("13px"); expect(tab.left).toBe("0px"); expect(tab.right).toBe("0px");
      expect(Math.abs(tab.underline - tab.width)).toBeLessThanOrEqual(1);
      if (tab.nextGap !== null) expect(tab.nextGap).toBeGreaterThanOrEqual(8);
    }
    await mkdir(captures, { recursive: true });
    await list.screenshot({ path: `${captures}/tabs-${locale}-288.png` });
    await writeFile(`${captures}/tabs-${locale}-288.json`, JSON.stringify(metrics, null, 2));
  });
});
