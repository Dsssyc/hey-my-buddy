import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { expect, type Locator, type Page } from "@playwright/test";
import { expectNoSidewaysScroll, open, settle, test } from "./preview";

const empty = "macro-66666666-6666-4666-8666-666666666666";
const folder = "macro-77777777-7777-4777-8777-777777777777";
const git = "macro-88888888-8888-4888-8888-888888888888";
const captures = fileURLToPath(new URL("../../../tmp/adr032-p6b", import.meta.url));
const words = {
  zh: { locale: "zh-CN", none: "检出", fixed: "固定结果", sealed: "封存证据", source: "整合来源", integration: "整合", verification: "核验结果", changes: "改动位置", original: "原文件夹", hint: "黑板不另存副本", clean: "干净的三方合并", adjusted: "有调整", search: "搜索标题、项目或 ID", cancel: "取消", remove: "删除宏任务…", empty: "没有已提交的微任务" },
  en: { locale: "en-US", none: "Checkout", fixed: "Fixed result", sealed: "Sealed evidence", source: "Integration source", integration: "Integration", verification: "Verification result", changes: "Changes in", original: "the original folder", hint: "The blackboard keeps no copy", clean: "Clean three-way merge", adjusted: "Adjusted", search: "Search titles, projects or IDs", cancel: "Cancel", remove: "Delete macro task…", empty: "No submitted micro tasks" },
};
async function expand(section: Locator) {
  const trigger = section.locator('[data-slot="collapsible-trigger"]').first();
  if (await trigger.getAttribute("data-state") === "closed") await trigger.click();
}
async function capture(page: Page, name: string) {
  await expect(page.locator('[data-slot="macro-list"] [data-macro-task-id]').first()).toBeVisible();
  await settle(page);
  await expectNoSidewaysScroll(page);
  await mkdir(captures, { recursive: true });
  await page.screenshot({ path: join(captures, `${name}.png`), animations: "disabled" });
}
for (const [language, w] of Object.entries(words)) for (const theme of ["dark", "light"] as const) for (const width of [1360, 800]) {
  test.describe(`ADR-032 ${language} ${theme} ${width}`, () => {
    test.use({ locale: w.locale, viewport: { width, height: 1200 }, colorScheme: theme });
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    });
    const suffix = `${language}-${theme}-${width}`;
    test("empty macro task keeps its columns, search and zero deletion scope", async ({ page }) => {
      await open(page, `#tasks/${empty}`);
      const stage = page.locator('[data-slot="timeline-empty-stage"]');
      await expect(stage.getByText(w.empty)).toBeVisible();
      await expect(stage.locator('[data-slot="timeline-row-count"]')).toHaveText("0");
      const header = page.locator('[data-slot="macro-overview-header"]');
      await expect(header.locator('[data-slot="macro-overview-members"]')).toContainText("0");
      await expect(header.locator('dt').filter({ hasText: /^Host$/ })).toHaveCount(0);
      await expect(page.locator(`[data-macro-task-id="${empty}"] [data-slot="state-mark"]`)).toHaveCount(0);
      await capture(page, `macros-${suffix}`);
      const search = page.getByRole("textbox", { name: w.search });
      await search.fill("梳理归档");
      await expect(page.locator('[data-slot="macro-list-total"]')).toHaveText("1");
      await expect(page.locator('[data-slot="macro-group-count"]')).toHaveText("1");
      if (width === 1360) await capture(page, `macros-search-${suffix}`);
      const trigger = header.getByRole("button", { name: /^(更多|More)$/ });
      await trigger.click();
      await page.getByRole("menuitem", { name: w.remove }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await expect(dialog.getByRole("button", { name: w.cancel })).toBeFocused();
      await expect(dialog).toContainText(language === "zh" ? "0 个，其中 0 个" : "0 total");
      if (width === 1360) await capture(page, `delete-empty-${suffix}`);
      await dialog.getByRole("button", { name: w.cancel }).click();
    });
    for (const access of ["read", "write"]) test(`none ${access} shows fixed result and native permissions`, async ({ page }) => {
      await open(page, `#tasks/${folder}/preview-run-none-${access}/2`);
      const pane = page.locator('[data-slot="detail-pane"]');
      await expect(pane.getByRole("group", { name: w.none, exact: true })).toContainText("none");
      const result = pane.getByRole("group", { name: w.fixed, exact: true });
      await expand(result);
      await expect(result).toContainText(`result-none-${access}`);
      await expect(result).toContainText("result-receipt.json");
      await expect(result).toContainText("SHA-256");
      await expect(pane.getByRole("group", { name: w.sealed, exact: true })).toHaveCount(0);
      await expect(pane.getByRole("group", { name: w.source, exact: true })).toHaveCount(0);
      const changes = pane.getByRole("group", { name: w.changes, exact: true });
      if (access === "read") await expect(changes).toHaveCount(0);
      else {
        await expect(changes).toHaveText(`${w.changes}${w.original}`);
        const value = changes.getByText(w.original, { exact: true });
        await value.focus();
        await expect(page.getByRole("tooltip")).toHaveCount(0);
        await expect(page.getByRole("tooltip")).toHaveText(w.hint);
        await expect(value).toBeFocused();
        await capture(page, `none-write-focus-${suffix}`);
        await value.blur();
        await page.mouse.move(0, 0);
        await expect(page.getByRole("tooltip")).toHaveCount(0);
        await value.hover();
        await expect(page.getByRole("tooltip")).toHaveText(w.hint);
        if (width === 1360) await capture(page, `none-write-hover-${suffix}`);
        await page.mouse.move(0, 0);
        await expect(page.getByRole("tooltip")).toHaveCount(0);
      }
      await capture(page, `none-${access}-${suffix}`);
    });
    for (const source of ["commits", "patch"]) test(`Git ${source} delivery and acceptance separate seals from integration sources`, async ({ page }) => {
        await open(page, `#tasks/${git}/preview-run-git-${source}/2`);
        const pane = page.locator('[data-slot="detail-pane"]');
        await expand(pane.getByRole("group", { name: w.sealed, exact: true }));
        await expand(pane.getByRole("group", { name: w.source, exact: true }));
        await expect(pane).toContainText(`art-git-${source}`);
        if (source === "commits") await capture(page, `git-delivery-${suffix}`);
        await page.evaluate(hash => { location.hash = hash; }, `#tasks/${git}/preview-run-git-${source}/3`);
        await expect(pane.locator("h3")).toHaveText(language === "zh" ? "验收" : "Acceptance");
        await expand(pane.getByRole("group", { name: w.sealed, exact: true }));
        await expand(pane.getByRole("group", { name: w.source, exact: true }));
        await expand(pane.getByRole("group", { name: w.integration, exact: true }));
        await expect(pane.getByRole("group", { name: language === "zh" ? "核对说明" : "Review note", exact: true })).toBeVisible();
        const verification = pane.getByRole("group", { name: w.verification, exact: true });
        await expect(verification).toContainText(source === "commits" ? w.clean : w.adjusted);
        if (source === "patch") {
          await expect(verification).toContainText("src/import/parse.py");
          await expect(verification).toContainText("docs/release.md");
        }
        await capture(page, `git-${source === "commits" ? "clean" : "adjusted"}-${suffix}`);
    });
  });
}
