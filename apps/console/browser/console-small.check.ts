import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, type Locator, type Page } from "@playwright/test";
import { open, test } from "./preview";

/**
 * The ADR-032 follow-up batch (2026-11-05): the workspace keeps the page ground
 * before and after a detail opens, the detail's header bar lines up with the
 * micro task column header, and a saved note leaves on its own after about four
 * seconds unless the reader asked for reduced motion.
 */
const macro = "macro-11111111-1111-4111-8111-111111111111";
const leads = "macro-22222222-2222-4222-8222-222222222222";
const MODEL = "codex / openai / gpt-5-codex";
const captures = fileURLToPath(new URL("../../../tmp/console-small", import.meta.url));

const token = (page: Page, name: string) => page.evaluate(value => {
  const probe = document.createElement("span");
  probe.style.backgroundColor = value;
  document.body.append(probe);
  const color = getComputedStyle(probe).backgroundColor;
  probe.remove();
  return color;
}, name);

/** The column header bar as the reader sees it, border line included. */
async function columnBar(page: Page): Promise<{ top: number; bottom: number; height: number; border: string }> {
  return page.locator('[data-slot=timeline-row-filter]').evaluate(node => {
    const bar = node.parentElement!.getBoundingClientRect();
    return { top: bar.top, bottom: bar.bottom, height: bar.height, border: getComputedStyle(node.parentElement!).borderBottomWidth };
  });
}
async function detailBar(page: Page): Promise<{ top: number; bottom: number; height: number; border: string }> {
  return page.locator('[data-slot=detail-header]').evaluate(node => {
    const bar = node.getBoundingClientRect();
    return { top: bar.top, bottom: bar.bottom, height: bar.height, border: getComputedStyle(node).borderBottomWidth };
  });
}

for (const width of [1360, 1024, 800]) for (const theme of ["dark", "light"]) {
  test.describe(`workspace ground and ruled header ${width}px ${theme}`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 900 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => { await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme); });

    test("keeps the page ground across opening and lines the two header bars up", async ({ page }) => {
      await open(page, `#tasks/${macro}`);
      const paper = await token(page, "var(--paper)");
      const surface = await token(page, "var(--surface)");
      const ruler = await token(page, "var(--ruler-ground)");
      // Before opening: the tracks are the workspace, the sidebar and header stay card.
      await expect(page.locator('[data-slot=timeline-viewport] > [data-slot=scroll-area]')).toHaveCSS("background-color", paper);
      await expect(page.locator('[data-slot=macro-list]')).toHaveCSS("background-color", surface);
      await expect(page.locator('[data-slot=macro-overview-header]')).toHaveCSS("background-color", surface);
      // Below 1360 the macro list is folded away first, so the two columns sit side by side.
      if (width < 1360) await page.getByRole("button", { name: "收起宏任务列表" }).click();
      await page.locator('[data-slot=timeline-tracks] [data-slot=timeline-row-name]').first().click();
      const detail = page.locator('[data-slot=task-detail]');
      await expect(detail).toBeVisible();
      // After opening: the item column, the detail and its strip are all the page ground.
      await expect(page.locator('[data-slot=timeline-detail-stage] > [data-slot=scroll-area]')).toHaveCSS("background-color", paper);
      await expect(detail).toHaveCSS("background-color", paper);
      await expect(page.locator('[data-slot=detail-pane]')).toHaveCSS("background-color", paper);
      await expect(page.locator('[data-slot=detail-step-band]')).toHaveCSS("background-color", paper);
      await expect(page.locator('[data-slot=timeline-status]')).toHaveCSS("background-color", surface);
      // The two header bars share the ruler's ground, height and bottom line.
      await expect(page.locator('[data-slot=timeline-row-filter]')).toHaveCSS("background-color", ruler);
      await expect(page.locator('[data-slot=detail-header]')).toHaveCSS("background-color", ruler);
      const column = await columnBar(page);
      const bar = await detailBar(page);
      expect(Math.abs(column.height - 37)).toBeLessThanOrEqual(0.5);
      expect(Math.abs(bar.height - column.height)).toBeLessThanOrEqual(0.5);
      expect(Math.abs(bar.top - column.top)).toBeLessThanOrEqual(1);
      expect(Math.abs(bar.bottom - column.bottom)).toBeLessThanOrEqual(1);
      expect(bar.border).toBe("1px");
      expect(column.border).toBe("1px");
      if (theme === "dark" && width === 1360) {
        mkdirSync(captures, { recursive: true });
        await page.screenshot({ path: `${captures}/header-align-${width}.png` });
      }
    });
  });
}

for (const theme of ["dark", "light"]) test.describe(`lead lanes keep the card ground ${theme}`, () => {
  test.use({ locale: "zh-CN", viewport: { width: 1360, height: 900 }, reducedMotion: "reduce" });
  test.beforeEach(async ({ page }) => { await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme); });

  test("distinguishes the lanes on both sides of opening a detail", async ({ page }) => {
    await open(page, `#tasks/${leads}`);
    const surface = await token(page, "var(--surface)");
    await expect(page.locator('[data-slot=timeline-lane]').first()).toHaveCSS("background-color", surface);
    await page.locator('[data-slot=timeline-tracks] [data-slot=timeline-row-name]').nth(1).click();
    await expect(page.locator('[data-slot=detail-pane]')).toBeVisible();
    await expect(page.locator('[data-slot=detail-lane]').first()).toHaveCSS("background-color", surface);
    if (theme === "dark") {
      mkdirSync(captures, { recursive: true });
      await page.screenshot({ path: `${captures}/lead-lane-${theme}.png` });
    }
  });
});

const notice = (page: Page) => page.getByRole("status").filter({ hasText: /^设置已保存$/ });
async function saveOnce(page: Page) {
  await page.route("**/api/command", async route => {
    const request = route.request().postDataJSON();
    if (request?.operation === "user_policy_publish") await route.fulfill({ json: { ok: true, result: { published: true } } });
    else await route.continue();
  });
  await open(page, "#buddies/catalog");
  await page.getByRole("button", { name: MODEL, exact: true }).click();
  const drawer = page.getByRole("region", { name: MODEL, exact: true });
  await drawer.getByRole("textbox", { name: "备注", exact: true }).fill("合成备注，保存后看提示怎样离开。");
  const saveBar = page.getByRole("region", { name: "未保存的修改" });
  await saveBar.getByRole("button", { name: /^保存$/ }).click();
  await expect(notice(page)).toBeVisible();
}

for (const motion of ["no-preference", "reduce"] as const) test.describe(`success notes leave on their own (${motion})`, () => {
  test.use({ locale: "zh-CN", viewport: { width: 1280, height: 900 }, reducedMotion: motion });
  test.beforeEach(async ({ page }) => { await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark")); });

  test("shows the saved note for about four seconds, then it is gone", async ({ page }) => {
    await saveOnce(page);
    await expect(notice(page)).toHaveCSS("opacity", "1");
    await page.waitForTimeout(3600);
    await expect(notice(page)).toHaveCSS("opacity", "1");
    if (motion === "no-preference") {
      // The last stretch fades the line out; one frame inside the page catches it part-way, for a capture.
      const midFade = await notice(page).evaluate(node => new Promise<number>(resolve => {
        const tick = () => {
          const opacity = Number(getComputedStyle(node).opacity);
          if (opacity < 0.95) resolve(opacity);
          else requestAnimationFrame(tick);
        };
        tick();
      }));
      expect(midFade).toBeGreaterThan(0);
      mkdirSync(captures, { recursive: true });
      await page.screenshot({ path: `${captures}/notice-mid-fade.png` });
      await expect(notice(page)).toHaveCount(0);
    } else {
      // Reduced motion never fades: the line is there, then it is simply gone.
      await page.waitForTimeout(700);
      await expect(notice(page)).toHaveCount(0);
    }
  });

  test("takes the showing note back the moment the reader edits again", async ({ page }) => {
    await saveOnce(page);
    await page.waitForTimeout(1000);
    const drawer = page.getByRole("region", { name: MODEL, exact: true });
    await drawer.getByRole("textbox", { name: "备注", exact: true }).fill("再改一下，提示应收起。");
    await expect(notice(page)).toHaveCount(0);
    await page.waitForTimeout(4500);
    await expect(notice(page)).toHaveCount(0);
  });
});

/** The none turns of the folder project name the buddy that ran them. */
test.describe("a turn without a checkout", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1360, height: 900 }, reducedMotion: "reduce" });
  for (const [theme, access] of [["dark", "write"], ["light", "read"]] as const) {
    test(`names its buddy and origin like a Git turn (${theme} ${access})`, async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/macro-77777777-7777-4777-8777-777777777777/preview-run-none-${access}/2`);
      const band = page.locator('[data-slot=detail-band]').first();
      await expect(band).toBeVisible();
      await expect(band).toContainText("codex / openai / gpt-5-codex / high");
      await expect(band).toContainText("未记录路由");
      if (theme === "dark") {
        mkdirSync(captures, { recursive: true });
        await page.screenshot({ path: `${captures}/none-turn-buddy.png` });
      }
    });
  }
});
