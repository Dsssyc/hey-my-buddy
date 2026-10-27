import { expect, type Page } from "@playwright/test";
import { expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, settle, test } from "./preview";
import { macroTaskTimelineFixture } from "../src/macro-task-fixtures";
import type { DeletionBlocker, MacroTaskDeletionPreview } from "../src/macro-task-deletion";

const title = "控制台前端重建：宏任务删除、确认范围与很长的微任务名称 / deletion scope and confirmation";
const authoredTitle = "未加载的作者标题 / original author title";
const taskTitle = "任务首行内容 / original task line";
const words = {
  "zh-CN": { more: "更多", action: "删除宏任务…", cancel: "取消", confirm: "删除", retry: "重试删除", acknowledge: "知道了",
    blocked: `还不能删除「${title}」`, heading: `删除「${title}」`, scope: "删除范围与拦截原因", review: "由 Host 验收、打回或给出结论", unknown: "停止未确认", deleted: `已删除「${title}」。`, unnamed: "未命名",
    knownScope: ["14 份，共 6.2 MB", "11 个，共 48.0 MB", "3 个检出，12 个引用"], mixedScope: ["0 份，共 0 B", "数量未知，体积未知", "0 个检出，引用数量未知"] },
  "en-US": { more: "More", action: "Delete macro task…", cancel: "Cancel", confirm: "Delete", retry: "Retry deletion", acknowledge: "Got it",
    blocked: `Cannot delete “${title}” yet`, heading: `Delete “${title}”`, scope: "Deletion scope and blocking reasons", review: "The Host must accept, return, or provide a conclusion", unknown: "Stop unconfirmed", deleted: `Deleted “${title}”.`, unnamed: "Unnamed",
    knownScope: ["14; total 6.2 MB", "11; total 48.0 MB", "Checkouts: 3; Git refs: 12"], mixedScope: ["0; total 0 B", "Count unknown; Size unknown", "Checkouts: 0; Git ref count unknown"] },
};

/** Browser-only fixtures mirror the engine's current replies; no board is opened. */
async function deletionFixture(page: Page, blocked = false, scope: MacroTaskDeletionPreview["scope"] = {
  artifacts: { count: 14, bytes: 6_500_000 }, sessions: { count: 11, bytes: 50_331_648 }, checkouts: 3, gitRefs: 12,
}) {
  const timeline = macroTaskTimelineFixture();
  timeline.macroTask.title = title;
  let deleted = false;
  let deleteAttempts = 0;
  const commands: { operation: string; params: Record<string, unknown> }[] = [];
  await page.route("**/api/macro-tasks?*", async route => {
    await route.fulfill({ json: { macroTasks: deleted ? [] : [timeline.macroTask],
      totals: { all: deleted ? 0 : 1, active: deleted ? 0 : 1, host: 0, review: 0 }, nextCursor: null, cursor: 41, changed: false } });
  });
  await page.route("**/api/macro-tasks/*/timeline?*", route => route.fulfill({ json: timeline }));
  await page.route("**/api/command", async route => {
    const body = route.request().postDataJSON() as typeof commands[number];
    if (!["macro_task_deletion_preview", "macro_task_delete"].includes(body.operation)) return route.fallback();
    commands.push(body);
    expect(route.request().headers()["x-hey-my-buddy-csrf"]).toBeTruthy();
    expect(body.params.macroTaskId).toBe(timeline.macroTask.macroTaskId);
    if (body.operation === "macro_task_deletion_preview") {
      expect(Object.keys(body.params)).toEqual(["macroTaskId"]);
      const kinds = ["running", "review", "stop-unconfirmed"] as const;
      const blockers = blocked ? Array.from({ length: 50 }, (_, index): DeletionBlocker => {
        const entry = { runId: `synthetic-run-${index}`, taskId: `synthetic-run-${index}`, state: "completed", runState: "delivered",
          reason: { code: `macro-task-deletion.${kinds[index % kinds.length]}` as const, params: {} }, attemptIds: [] };
        return index % 3 === 2 ? { ...entry, title: null, titleSource: "none" }
          : { ...entry, title: `${index % 3 === 0 ? authoredTitle : taskTitle} ${index}`, titleSource: index % 3 === 0 ? "title" : "task" };
      }) : [];
      const preview: MacroTaskDeletionPreview = { macroTaskId: timeline.macroTask.macroTaskId, microTaskCount: 73,
        unconcludedFailureCount: 2, scope, blockerTotal: blocked ? 51 : 0, blockers, backupRetention: "until-backup-rotation", deletionState: null };
      return route.fulfill({ json: { ok: true, result: preview } });
    }
    expect(body.params).toEqual({ macroTaskId: timeline.macroTask.macroTaskId, microTaskCount: 73, confirm: true });
    expect(blocked).toBe(false);
    ++deleteAttempts;
    const receipt = { deletionId: "del-browser-fixture", macroTaskId: timeline.macroTask.macroTaskId,
      microTaskCount: 73, duplicate: deleteAttempts > 1 };
    if (deleteAttempts === 1) return route.fulfill({ json: { ok: true, result: { ...receipt, state: "deleting", deletedAt: null,
      reason: { code: "macro-task-deletion.physical-failed", params: { errorCode: "IO_ERROR" } }, objectKind: "native-session" } } });
    deleted = true;
    await route.fulfill({ json: { ok: true, result: { ...receipt, state: "deleted", deletedAt: "2026-10-18T08:00:00Z" } } });
  });
  return commands;
}

for (const locale of ["zh-CN", "en-US"] as const) for (const width of [1280, 760, 320]) for (const theme of ["light", "dark"] as const) {
  test.describe(`macro deletion ${locale} ${width}px ${theme}`, () => {
    test.use({ locale, viewport: { width, height: 800 }, colorScheme: theme });
    test("keeps confirmation readable, traps focus, cancels and retries the fixed deletion", async ({ page }, testInfo) => {
      const w = words[locale];
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      const commands = await deletionFixture(page);
      await open(page, "#tasks/macro-1");
      const more = page.getByRole("button", { name: w.more, exact: true });
      await more.click();
      await page.getByRole("button", { name: w.action, exact: true }).click();
      let dialog = page.getByRole("dialog", { name: w.heading, exact: true });
      await expect(dialog.getByRole("button", { name: w.cancel, exact: true })).toBeFocused();
      await expect(dialog.getByRole("button", { name: w.confirm, exact: true })).toBeVisible();
      expect((await dialog.locator("[data-slot=macro-delete-scope] dd").allTextContents()).slice(1)).toEqual(w.knownScope);
      await settle(page);
      await expectWholeAndApart(page.locator("body"), dialog);
      await expectTextUncut(dialog.locator("h2, p, dt, dd, button"));
      await expectWholeAndApart(dialog.locator("[data-slot=macro-delete-actions]"), dialog.locator("[data-slot=macro-delete-actions] button"));
      await expectNoSidewaysScroll(page);
      if (locale === "zh-CN" && width !== 760) await page.screenshot({ path: testInfo.outputPath(`batch3-delete-confirm-${width}-${theme}.png`) });
      for (let index = 0; index < 6; index++) {
        await page.keyboard.press("Tab");
        expect(await dialog.evaluate(node => node.contains(document.activeElement))).toBe(true);
      }
      await page.keyboard.press("Escape");
      await expect(dialog).toHaveCount(0);
      await expect(more).toBeFocused();
      expect(commands.every(command => command.operation === "macro_task_deletion_preview")).toBe(true);
      await more.click();
      await page.getByRole("button", { name: w.action, exact: true }).click();
      dialog = page.getByRole("dialog", { name: w.heading, exact: true });
      await dialog.getByRole("button", { name: w.confirm, exact: true }).click();
      await expect(dialog.getByRole("button", { name: w.retry, exact: true })).toBeVisible();
      await expectTextUncut(dialog.locator("h2, p, button"));
      await dialog.getByRole("button", { name: w.retry, exact: true }).click();
      await expect(dialog).toHaveCount(0);
      await expect(page).toHaveURL(/#tasks$/);
      await expect(page.getByText(w.deleted, { exact: true })).toBeVisible();
      await expect(page.locator("[data-slot=macro-entry]")).toHaveCount(0);
      const writes = commands.filter(command => command.operation === "macro_task_delete");
      expect(writes).toHaveLength(2);
      expect(writes[1]).toEqual(writes[0]);
    });

    test("distinguishes known zero from unknown scope facts", async ({ page }, testInfo) => {
      const w = words[locale];
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      const commands = await deletionFixture(page, false, { artifacts: { count: 0, bytes: 0 }, sessions: { count: null, bytes: null }, checkouts: 0, gitRefs: null });
      await open(page, "#tasks/macro-1");
      await page.getByRole("button", { name: w.more, exact: true }).click();
      await page.getByRole("button", { name: w.action, exact: true }).click();
      const dialog = page.getByRole("dialog", { name: w.heading, exact: true });
      await expect(dialog.getByRole("button", { name: w.confirm, exact: true })).toBeVisible();
      expect((await dialog.locator("[data-slot=macro-delete-scope] dd").allTextContents()).slice(1)).toEqual(w.mixedScope);
      await expect(dialog.getByRole("button", { name: w.cancel, exact: true })).toBeFocused();
      await settle(page);
      await expectWholeAndApart(page.locator("body"), dialog);
      await expectTextUncut(dialog.locator("h2, dt, dd, button"));
      await expectNoSidewaysScroll(page);
      await dialog.getByRole("button", { name: w.cancel, exact: true }).click();
      expect(commands.every(command => command.operation === "macro_task_deletion_preview")).toBe(true);
    });

    test("shows bounded unsettled evidence with a scrollable body and reachable acknowledgement", async ({ page }, testInfo) => {
      const w = words[locale];
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      const commands = await deletionFixture(page, true);
      await open(page, "#tasks/macro-1");
      await page.getByRole("button", { name: w.more, exact: true }).click();
      await page.getByRole("button", { name: w.action, exact: true }).click();
      const dialog = page.getByRole("dialog", { name: w.blocked, exact: true });
      await expect(dialog.getByRole("button", { name: w.acknowledge, exact: true })).toBeFocused();
      await expect(dialog.getByRole("button", { name: w.confirm, exact: true })).toHaveCount(0);
      await expect(dialog.getByText(`${authoredTitle} 0`, { exact: true })).toBeVisible();
      await expect(dialog.getByText(`${taskTitle} 1`, { exact: true })).toBeVisible();
      await expect(dialog.getByText(w.unnamed, { exact: true }).first()).toBeVisible();
      await expect(dialog.getByText(w.review, { exact: true }).first()).toBeVisible();
      await expect(dialog.getByText(w.unknown, { exact: true }).first()).toBeVisible();
      await settle(page);
      await expectWholeAndApart(page.locator("body"), dialog);
      await expectWholeAndApart(dialog, dialog.locator("[data-slot=macro-delete-actions]"));
      const viewport = dialog.getByLabel(w.scope, { exact: true });
      expect(await viewport.evaluate(node => node.scrollHeight > node.clientHeight)).toBe(true);
      await viewport.focus();
      await page.keyboard.press("End");
      await expect.poll(() => viewport.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
      await expectTextUncut(dialog.locator("h2, [data-slot=macro-delete-actions] button"));
      await expectNoSidewaysScroll(page);
      if (locale === "zh-CN" && width !== 760) await page.screenshot({ path: testInfo.outputPath(`batch3-delete-blocked-${width}-${theme}.png`) });
      await dialog.getByRole("button", { name: w.acknowledge, exact: true }).click();
      expect(commands.every(command => command.operation === "macro_task_deletion_preview")).toBe(true);
    });
  });
}
