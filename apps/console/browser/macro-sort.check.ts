import { expect, type Page, type Locator } from "@playwright/test";
import { open, settle, test } from "./preview";
import type { MacroTaskPage, MacroTaskSort } from "../src/macro-task-types";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";
const alpha = "project:synthetic-preview-alpha", beta = "project:synthetic-preview-beta";
const group = (page: Page, id: string) => page.locator(`[data-slot=macro-group][data-group-id="${id}"]`);
const rows = (target: Locator) => target.locator("[data-macro-task-id]").evaluateAll(nodes => nodes.map(node => node.getAttribute("data-macro-task-id")));

for (const locale of ["zh-CN", "en-US"] as const) for (const theme of ["dark", "light"] as const) {
  test.describe(`project sorting ${locale} ${theme}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
    test("keeps project choices independent across paging, reload and navigation history", async ({ page }) => {
      const zh = locale === "zh-CN";
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/${A}?sort=created-asc`);
      await expect(page).not.toHaveURL(/sort=/);
      const projects = page.locator("[data-slot=macro-group]");
      await expect(projects).toHaveCount(3);
      const initialOrder = await projects.evaluateAll(nodes => nodes.map(node => node.getAttribute("data-group-id")));
      const originalBeta = await rows(group(page, beta));
      const historyLength = await page.evaluate(() => history.length);
      const toolbar = page.locator("[data-slot=macro-list-toolbar] > div").last();
      await expect(toolbar.getByRole("button")).toHaveCount(2);
      await expect(toolbar.getByRole("button").first()).toHaveAccessibleName(zh ? "全部折叠" : "Collapse all");
      await expect(toolbar.getByRole("button").last()).toHaveAccessibleName(zh ? "收起宏任务列表" : "Collapse macro task list");

      async function choose(id: string, name: string, mode: MacroTaskSort) {
        const target = group(page, id);
        const heading = target.locator("[data-slot=macro-group-heading]");
        if (await heading.getAttribute("aria-expanded") === "false") await heading.click();
        await heading.hover();
        await target.locator("[data-slot=macro-sort-button]").click();
        const menu = page.locator("[data-slot=macro-sort-menu]");
        await expect(menu.getByRole("group")).toHaveCount(2);
        await expect(menu.getByRole("menuitemradio", { checked: true })).toHaveCount(2);
        const response = page.waitForResponse(response => {
          const url = new URL(response.url());
          return url.pathname === "/api/macro-tasks" && url.searchParams.get("group") === id && url.searchParams.get("sort") === mode && !url.searchParams.has("before");
        });
        await menu.getByRole("menuitemradio", { name, exact: true }).click();
        const body = await (await response).json() as MacroTaskPage;
        const ordered = body.groups[0].macroTasks;
        await expect.poll(() => rows(target)).toEqual(ordered.map(row => row.macroTaskId));
        await expect(menu).toBeHidden();
        await expect(page).not.toHaveURL(/sort=/);
        return body.groups[0];
      }
      await choose(alpha, zh ? "创建时间" : "Created", "created-desc");
      await expect.poll(() => rows(group(page, beta))).toEqual(originalBeta);
      const ascending = await choose(alpha, zh ? "升序" : "Ascending", "created-asc");
      expect(await projects.evaluateAll(nodes => nodes.map(node => node.getAttribute("data-group-id")))).toEqual(initialOrder);
      const moreResponse = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname === "/api/macro-tasks" && url.searchParams.get("group") === alpha && url.searchParams.get("before") === ascending.nextCursor;
      });
      await group(page, alpha).locator("[data-slot=macro-group-more]").click();
      const more = await moreResponse;
      expect(new URL(more.url()).searchParams.get("sort")).toBe("created-asc");
      const tail = await more.json() as MacroTaskPage;
      await expect.poll(() => rows(group(page, alpha))).toEqual([...ascending.macroTasks, ...tail.groups[0].macroTasks].map(row => row.macroTaskId));
      await choose(beta, zh ? "升序" : "Ascending", "activity-asc");
      await choose("standalone", zh ? "创建时间" : "Created", "created-desc");
      expect(await page.evaluate(() => history.length)).toBe(historyLength);
      expect(await page.evaluate(() => Object.fromEntries(Object.entries(localStorage).filter(([key]) => key.startsWith("hey-my-buddy.console.view.macro-task-sort:"))))).toEqual({
        [`hey-my-buddy.console.view.macro-task-sort:${alpha}`]: "created-asc",
        [`hey-my-buddy.console.view.macro-task-sort:${beta}`]: "activity-asc",
        "hey-my-buddy.console.view.macro-task-sort:standalone": "created-desc",
      });
      await page.reload();
      await expect.poll(() => rows(group(page, alpha))).toEqual(ascending.macroTasks.map(row => row.macroTaskId));
      for (const [id, choices] of [[alpha, [zh ? "创建时间" : "Created", zh ? "升序" : "Ascending"]], [beta, [zh ? "最近活动" : "Last activity", zh ? "升序" : "Ascending"]], ["standalone", [zh ? "创建时间" : "Created", zh ? "降序" : "Descending"]]] as const) {
        const trigger = group(page, id).locator("[data-slot=macro-sort-button]");
        await trigger.focus(); await trigger.press("ArrowDown");
        for (const name of choices) await expect(page.getByRole("menuitemradio", { name, exact: true })).toHaveAttribute("aria-checked", "true");
        await page.keyboard.press("Escape");
        await expect(trigger).toBeFocused();
      }
      await group(page, beta).locator(`[data-macro-task-id="${B}"] [data-slot=macro-entry]`).click();
      await expect(page).toHaveURL(new RegExp(`#tasks/${B}$`));
      await page.goBack();
      await expect(page).toHaveURL(new RegExp(`#tasks/${A}$`));
      await expect.poll(() => rows(group(page, alpha))).toEqual(ascending.macroTasks.map(row => row.macroTaskId));
      await page.goForward();
      await expect(page).toHaveURL(new RegExp(`#tasks/${B}$`));
      await expect(page).not.toHaveURL(/sort=/);
      await toolbar.getByRole("button").first().click();
      await expect(page.locator('[data-slot=macro-group-heading][aria-expanded=true]')).toHaveCount(0);
      await settle(page);
    });
  });
}
