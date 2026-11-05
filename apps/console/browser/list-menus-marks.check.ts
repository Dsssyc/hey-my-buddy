import { expect, type Locator, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, openLegend, settle, test } from "./preview";
import type { MacroTaskPage } from "../src/macro-task-types";

const A = "macro-11111111-1111-4111-8111-111111111111";
const shots = fileURLToPath(new URL("../../../tmp/list-menus-marks/", import.meta.url));
const entry = (page: Page, id: string) => page.locator(`[data-slot=macro-list] [data-macro-task-id="${id}"]`);
const opacity = (button: Locator) => button.evaluate(node => getComputedStyle(node.parentElement!).opacity);

async function aligned(page: Page, trigger: Locator, menu: Locator, side: "bottom" | "top" = "bottom") {
  await settle(page);
  await expect(menu).toHaveAttribute("data-side", side);
  const button = (await trigger.boundingBox())!, box = (await menu.boundingBox())!;
  expect(box.x + box.width).toBeCloseTo(button.x + button.width, 0);
  expect(side === "bottom" ? box.y - button.y - button.height : button.y - box.y - box.height).toBeCloseTo(4, 0);
  await expect(menu).toHaveCSS("border-top-left-radius", "6px");
}

for (const [locale, theme] of [["zh-CN", "dark"], ["zh-CN", "light"], ["en-US", "dark"]] as const) {
  test.describe(`list menus and waiting marks ${locale} ${theme}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 960 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await mkdir(shots, { recursive: true });
    });

    test("reserves the entry button, swaps the project count, and keeps stop results read-only", async ({ page }) => {
      const zh = locale === "zh-CN", suffix = `${zh ? "zh" : "en"}-${theme}-1440.png`;
      await page.route("**/api/command", route => route.request().postDataJSON().operation === "macro_task_stop"
        ? route.fulfill({ json: { ok: false, error: { code: "FORBIDDEN", message: "Synthetic stop refusal" } } })
        : route.fallback());
      await open(page, `#tasks/${A}`);
      const selected = entry(page, A), selectedButton = selected.locator("[data-slot=macro-menu-button]");
      await expect(selected).toHaveAttribute("data-selected", "true");
      await page.mouse.move(1400, 900);
      await expect.poll(() => opacity(selectedButton)).toBe("1");
      const other = page.locator('[data-slot=macro-list] [data-macro-task-id][data-selected=false]').first();
      const otherButton = other.locator("[data-slot=macro-menu-button]"), title = other.locator("[data-slot=macro-entry-title]");
      await expect.poll(() => opacity(otherButton)).toBe("0");
      const before = await title.boundingBox();
      await other.hover();
      await expect.poll(() => opacity(otherButton)).toBe("1");
      expect(await title.boundingBox()).toEqual(before);
      const titleBox = (await title.boundingBox())!, buttonBox = (await otherButton.boundingBox())!;
      expect(buttonBox.width).toBe(24);
      expect(buttonBox.x - titleBox.x - titleBox.width).toBeCloseTo(8, 0);
      await page.mouse.move(1400, 900);
      await expect.poll(() => opacity(otherButton)).toBe("0");
      await other.locator("[data-slot=macro-entry]").focus();
      await expect.poll(() => opacity(otherButton)).toBe("1");
      expect(await title.boundingBox()).toEqual(before);
      await page.keyboard.press("Tab");
      await expect(otherButton).toBeFocused();
      await otherButton.press("ArrowDown");
      await expect(page.getByRole("menu")).toBeVisible();
      await page.keyboard.press("End");
      await expect(page.getByRole("menuitem", { name: zh ? "删除宏任务…" : "Delete macro task…" })).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(otherButton).toBeFocused();

      const project = selected.locator("xpath=ancestor::*[@data-slot='macro-group']");
      const heading = project.locator("[data-slot=macro-group-heading]"), count = project.locator("[data-slot=macro-group-count]"), sort = project.locator("[data-slot=macro-sort-button]");
      await selectedButton.blur(); await otherButton.blur();
      await page.mouse.move(1400, 900);
      await expect(count).toHaveCSS("opacity", "1");
      await heading.hover();
      await expect(count).toHaveCSS("opacity", "0");
      await sort.click();
      await aligned(page, sort, page.locator("[data-slot=macro-sort-menu]"));
      await expect(page.getByRole("group", { name: zh ? "排序依据" : "Sort by" })).toBeVisible();
      await expect(page.getByRole("group", { name: zh ? "顺序" : "Order" })).toBeVisible();
      await page.screenshot({ path: `${shots}/project-menu-${suffix}` });
      await page.keyboard.press("Escape");

      await selected.hover(); await selectedButton.click();
      const menu = page.locator("[data-slot=macro-entry-menu]");
      await aligned(page, selectedButton, menu);
      await expect(menu.getByRole("menuitem")).toHaveCount(2);
      await page.screenshot({ path: `${shots}/entry-menu-${suffix}` });
      const header = page.locator("[data-slot=macro-detail]");
      await expect(header.locator("[data-slot=macro-menu-button]")).toHaveCount(1);
      await expect(header.locator("[data-slot=macro-stop-action], [data-slot=macro-delete-action]")).toHaveCount(0);
      await menu.getByRole("menuitem", { name: zh ? "停止宏任务" : "Stop macro task", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: zh ? "停止宏任务" : "Stop macro task", exact: true });
      await dialog.getByRole("button", { name: zh ? "确认停止宏任务" : "Confirm stop", exact: true }).click();
      const status = page.locator("[data-slot=macro-stop-state]");
      await expect(status).toHaveText(zh ? "停止请求未提交" : "Stop request was refused");
      await expect(status.locator("button")).toHaveCount(0);
      await expect(header.getByRole("button", { name: zh ? "知道了" : "Dismiss", exact: true })).toHaveCount(0);
      await page.mouse.move(1400, 900); await settle(page);
      await page.screenshot({ path: `${shots}/stop-state-${suffix}` });
      await selectedButton.click();
      await expect(menu.getByRole("menuitem", { name: zh ? "知道了" : "Dismiss", exact: true })).toBeVisible();
      await menu.getByRole("menuitem", { name: zh ? "知道了" : "Dismiss", exact: true }).click();
      await expect(status).toHaveCount(0);
    });

    test("distinguishes both orange diamonds in counts, rows, tracks, legend and micro task detail", async ({ page }) => {
      await open(page, `#tasks/${A}`);
      const host = page.locator('[data-slot=macro-overview-count-host] [data-slot=state-mark]');
      const review = page.locator('[data-slot=macro-overview-count-review] [data-slot=state-mark]');
      await expect(host).toHaveAttribute("data-kind", "hand");
      await expect(review).toHaveAttribute("data-kind", "review");
      await expect(review.locator("path")).toHaveCSS("fill", "none");
      const orange = await host.locator("path").evaluate(node => getComputedStyle(node).fill);
      await expect(review.locator("path")).toHaveCSS("stroke", orange);
      const tracks = page.locator('[data-slot=timeline-track]');
      await expect(tracks.locator('[data-slot=waiting-diamond][data-kind=hand]').first()).toBeVisible();
      await expect(tracks.locator('[data-slot=waiting-diamond][data-kind=review]').first()).toHaveCSS("fill", "none");
      const legend = await openLegend(page);
      await expect(legend.locator('[data-kind=waiting] [data-slot=waiting-diamond]')).toHaveCSS("fill", orange);
      await expect(legend.locator('[data-kind=review] [data-slot=waiting-diamond]')).toHaveCSS("fill", "none");
      await expect(legend.locator('[data-kind=review] [data-slot=waiting-diamond]')).toHaveCSS("stroke", orange);
      await settle(page);
      await page.screenshot({ path: `${shots}/waiting-legend-${locale === "zh-CN" ? "zh" : "en"}-${theme}-1440.png` });
      await page.keyboard.press("Escape");
      const delivered = page.locator('[data-slot=timeline-row-name]').filter({ has: page.locator('[data-slot=state-mark][data-kind=review]') }).first();
      await delivered.click();
      await expect(page.locator('[data-slot=detail-status] [data-slot=state-mark]')).toHaveAttribute("data-kind", "review");
      await expect(page.locator('[data-slot=detail-status] path')).toHaveCSS("fill", "none");
      await page.keyboard.press("Escape");
      await page.locator('[data-slot=macro-filter-tabs]').getByRole("tab", { name: locale === "zh-CN" ? /等待验收/ : /Awaiting acceptance/ }).click();
      const standalone = page.locator('[data-group-id=standalone] [data-slot=macro-group-heading]');
      await expect(standalone).toBeVisible();
      if (await standalone.getAttribute("aria-expanded") === "false") await standalone.click();
      await expect(page.locator('[data-slot=macro-entry] [data-slot=state-mark][data-kind=review]').first()).toBeVisible();
    });

    test("flips both list menus above their buttons when the bottom has no room", async ({ page }) => {
      await page.route("**/api/macro-tasks?*", async route => {
        const response = await route.fetch(), body = await response.json() as MacroTaskPage;
        const first = body.groups[0], row = first.macroTasks[0];
        const extra = Array.from({ length: 12 }, (_, index) => ({ ...first, id: `project:menu-${index}`, project: { ...first.project, id: `menu-${index}`, label: `Menu ${index}`, path: null }, total: 1, nextCursor: null,
          macroTasks: [{ ...row, macroTaskId: `macro-menu-${index}`, title: `Menu entry ${index}` }] }));
        await route.fulfill({ json: { ...body, groups: [...body.groups, ...extra] } });
      });
      await open(page, `#tasks/${A}`);
      const viewport = page.locator('[data-slot=macro-list] [data-slot=scroll-area-viewport]');
      await page.locator('[data-group-id="project:menu-11"]').waitFor();
      await viewport.focus(); await viewport.press("End"); await settle(page);
      const last = page.locator('[data-group-id="project:menu-11"]');
      await last.locator("[data-slot=macro-group-heading]").hover();
      const sort = last.locator("[data-slot=macro-sort-button]");
      await sort.click();
      await aligned(page, sort, page.locator("[data-slot=macro-sort-menu]"), "top");
      await page.keyboard.press("Escape");
      const more = last.locator("[data-slot=macro-menu-button]");
      await last.locator("[data-slot=macro-entry]").hover(); await more.click();
      await aligned(page, more, page.locator("[data-slot=macro-entry-menu]"), "top");
    });
  });
}
