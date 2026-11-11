import { randomUUID } from "node:crypto";
import { expect, test, type Locator, type Page } from "@playwright/test";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, settle } from "./preview";

const caseIds = new WeakMap<Page, string>();
test.beforeEach(async ({ page }) => {
  const id = randomUUID();
  caseIds.set(page, id);
  await page.context().setExtraHTTPHeaders({ "X-HMB-Preview-Case": id });
});
const caseHeaders = (page: Page) => ({ "X-HMB-Preview-Case": caseIds.get(page)! });

const MACRO = "macro-22222222-2222-4222-8222-222222222222";
const TIMELINE = `/api/macro-tasks/${MACRO}/timeline`;
const LEAD = "preview-run-b3";
const WAITING = "preview-run-b4";
const QUEUED = "preview-run-b5";

async function openLeadMacro(page: Page, language: string, tasks: string) {
  await page.clock.setFixedTime(new Date("2026-09-27T10:00:00.000Z"));
  await open(page, "#tasks");
  await expect(page.locator("html")).toHaveAttribute("lang", language);
  await expect(page.getByRole("navigation").first().getByRole("link", { name: new RegExp(`^${tasks}`) })).toBeVisible();
  const read = page.waitForResponse(response => new URL(response.url()).pathname === TIMELINE);
  await page.locator(`[data-macro-task-id="${MACRO}"] [data-slot=macro-entry]`).click();
  const response = await read;
  expect(response.status()).toBe(200);
  expect(response.headers()["x-hey-my-buddy-preview"]).toBe("synthetic-fixture-data");
  const body = await response.text();
  const timeline = JSON.parse(body) as MacroTaskTimeline;
  expect(timeline.scopeComplete).toBe(true);
  return { body, timeline };
}

function row(page: Page, runId: string) {
  return page.locator(`[data-slot=timeline-row-name][data-key="row:${runId}"]`);
}

/** The model suffix may ellipsize; the complete state must fit before it. */
async function expectReadableStart(target: Locator, text: string) {
  await expect(target).toBeVisible();
  const measured = await target.evaluate((element, prefix) => {
    const range = document.createRange();
    range.setStart(element.firstChild!, 0);
    range.setEnd(element.firstChild!, prefix.length);
    const ink = range.getBoundingClientRect(), frame = element.getBoundingClientRect();
    return {
      prefix: element.textContent!.slice(0, prefix.length),
      fits: ink.width > 0 && ink.height > 0 && ink.left >= frame.left - 0.5
        && ink.right <= frame.right + 0.5 && ink.top >= frame.top - 0.5 && ink.bottom <= frame.bottom + 0.5,
    };
  }, text);
  expect(measured, `${text} must be readable inside its frame`).toEqual({ prefix: text, fits: true });
}

function observeErrors(page: Page) {
  const errors = { page: [] as string[], console: [] as string[] };
  page.on("pageerror", error => errors.page.push(String(error)));
  page.on("console", message => { if (message.type() === "error") errors.console.push(message.text()); });
  return errors;
}

// The same Lead facts retain their meaning in both interface languages.
for (const { locale, language, tasks } of [
  { locale: "zh-CN", language: "zh-CN", tasks: "任务" },
  { locale: "en-US", language: "en", tasks: "Tasks" },
]) for (const width of [1440, 1024]) {
  test.describe(`Lead in the real synthetic preview in ${locale} at ${width}px`, () => {
    test.use({ locale, viewport: { width, height: 1000 } });

    test("keeps both waiting states readable and opens their recorded facts with the keyboard", async ({ page }) => {
      const errors = observeErrors(page);
      const { timeline } = await openLeadMacro(page, language, tasks);
      const zh = language === "zh-CN";
      const ownedState = zh ? "等待名下的微任务" : "Waiting for owned micro tasks";
      const leadState = zh ? "等待 Lead" : "Waiting for Lead";
      expect(timeline.rows.find(item => item.runId === LEAD)?.state).toBe("waiting-owned");
      expect(timeline.rows.find(item => item.runId === WAITING)?.leadOwnership).toMatchObject({
        leadRunId: LEAD, leadTurnId: "turn-att-b3-1",
      });
      expect(timeline.rows.find(item => item.runId === QUEUED)?.status).toBe("queued");
      for (const [runId, state] of [[LEAD, ownedState], [WAITING, leadState]]) {
        const label = row(page, runId);
        await expect(label).toHaveAccessibleName(new RegExp(state));
        await expectReadableStart(label.getByText(state, { exact: true }), state);
        await expectWholeAndApart(label, label.locator(":scope > span"));
      }

      await row(page, LEAD).focus();
      await expect(page.locator("[data-slot=timeline-status]")).toContainText(ownedState);
      await row(page, WAITING).focus();
      await row(page, WAITING).press("Enter");
      // Enter opens the micro task on the decision it waits for: the Lead's, with the request as recorded.
      const detail = page.locator("[data-slot=task-detail]");
      await expect(detail).toBeVisible();
      await expect(page.locator("[data-slot=detail-strip] [aria-pressed=true]")).toHaveAccessibleName(new RegExp(zh ? "等待 Lead" : "Waiting for Lead"));
      const request = detail.locator("[data-slot=detail-pane]");
      await expect(request).toContainText("需要 Lead 决定路径样例的核对范围");
      await expect(request).toContainText("由 Lead 确定后续核对范围");
      await expectTextUncut(request.locator("h3, dt, dd, p"));
      await expectNoSidewaysScroll(page);
      expect(errors).toEqual({ page: [], console: [] });

    });

    test("confirms the Lead macro task end scope and reads its conclusion from the updated view", async ({ page }) => {
      const errors = observeErrors(page);
      const commands: { operation: string; params: { macroTaskId: string; commandId: string } }[] = [];
      page.on("request", request => {
        if (request.method() === "POST" && new URL(request.url()).pathname === "/api/command"
          && request.postDataJSON().operation === "macro_task_end") commands.push(request.postDataJSON());
      });
      const { timeline } = await openLeadMacro(page, language, tasks);
      const zh = language === "zh-CN";
      const more = page.locator(`[data-macro-task-id="${MACRO}"] [data-slot=macro-menu-button]`);
      await more.click();
      await page.getByRole("menuitem", { name: zh ? "结束" : "End", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: zh ? "结束宏任务" : "End macro task", exact: true });
      const summary = timeline.macroTask;
      await expect(dialog.locator("#end-macro-task-name")).toHaveText(summary.title!);
      await expect(dialog.locator("#end-macro-task-counts li")).toHaveCount(2);
      await expect(dialog.locator("#end-macro-task-counts")).toContainText(zh
        ? `结束 ${summary.counts.unfinished} 个微任务`
        : `End ${summary.counts.unfinished} micro tasks`);
      await expect(dialog.locator("#end-macro-task-counts")).toContainText(zh
        ? `保留 ${summary.counts.delivered} 个已交付微任务`
        : `Keep ${summary.counts.delivered} delivered micro tasks`);
      await expect(dialog.getByRole("button", { name: zh ? "取消" : "Cancel", exact: true })).toBeFocused();
      await settle(page);
      await expectWholeAndApart(dialog, dialog.locator(":scope > h2, :scope > p, :scope > ul, :scope > [data-slot=end-dialog-actions]"));
      await expectWholeAndApart(dialog.locator("[data-slot=end-dialog-actions]"), dialog.getByRole("button"));
      await expectTextUncut(dialog.locator("h2, #end-macro-task-body, li, button"));
      await dialog.press("Escape");
      await expect(dialog).toBeHidden();
      expect(commands).toEqual([]);

      await more.click();
      await page.getByRole("menuitem", { name: zh ? "结束" : "End", exact: true }).click();
      const responsePromise = page.waitForResponse(response => response.request().method() === "POST"
        && new URL(response.url()).pathname === "/api/command"
        && response.request().postDataJSON().operation === "macro_task_end");
      await dialog.getByRole("button", { name: zh ? "确认结束" : "End", exact: true }).click();
      const response = await responsePromise;
      expect(response.status()).toBe(200);
      expect((await response.json()).result).toEqual({ macroTaskId: MACRO, duplicate: false });
      expect(commands).toEqual([{ operation: "macro_task_end", params: { macroTaskId: MACRO, commandId: expect.any(String) } }]);
      expect(commands[0]!.params.commandId).not.toBe("");
      await expect(dialog).toBeHidden();
      await expect(page.locator("[data-slot=macro-work-state]")).toHaveCount(0);
      const updatedResponse = await page.request.get(TIMELINE, { headers: caseHeaders(page) });
      expect(updatedResponse.status()).toBe(200);
      const updated = await updatedResponse.json() as MacroTaskTimeline;
      expect(updated.macroTask.counts.unfinished).toBeLessThan(summary.counts.unfinished);
      expect(updated.rows.filter(item => item.state === "concluded").length)
        .toBeGreaterThan(timeline.rows.filter(item => item.state === "concluded").length);
      const concluded = updated.rows.find(item => item.state === "concluded")!;
      await expect(row(page, concluded.runId)).toHaveAccessibleName(new RegExp(zh ? "已结束" : "Ended"));
      await expectNoSidewaysScroll(page);
      expect(errors.page).toEqual([]);
      expect(errors.console).toEqual([]);
    });
  });
}

for (const locale of ["zh-CN", "en-US"]) test.describe(`a long end title in ${locale} at narrow width`, () => {
  test.use({ locale, viewport: { width: 390, height: 844 } });
  test("wraps the title and keeps the scope and controls readable", async ({ page }) => {
    const title = "核对完整范围与全部已交付结果的宏任务标题：".repeat(10);
    await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
      const response = await route.fetch();
      const data = await response.json();
      const target = data.groups.flatMap((group: { macroTasks: { macroTaskId: string }[] }) => group.macroTasks)
        .find((item: { macroTaskId: string }) => item.macroTaskId === MACRO);
      target.title! = title;
      await route.fulfill({ response, json: data });
    });
    await open(page, "#tasks");
    await page.locator(`[data-macro-task-id="${MACRO}"] [data-slot=macro-entry]`).click();
    await page.locator("[data-slot=macro-overview-header]").getByRole("button", { name: locale === "zh-CN" ? "‹ 宏任务" : "‹ Macro tasks", exact: true }).click();
    await page.locator(`[data-macro-task-id="${MACRO}"] [data-slot=macro-menu-button]`).click();
    await page.getByRole("menuitem", { name: locale === "zh-CN" ? "结束" : "End", exact: true }).click();
    const dialog = page.getByRole("dialog");
    const titleLine = dialog.locator("#end-macro-task-name");
    await settle(page);
    await expect(titleLine).not.toHaveAttribute("aria-description");
    await expect(titleLine).toHaveText(title);
    await expect(dialog).toHaveAccessibleDescription(new RegExp(title.slice(0, 24)));
    expect(await titleLine.evaluate(element => {
      const range = document.createRange(); range.selectNodeContents(element);
      return getComputedStyle(element).whiteSpace === "normal" && range.getClientRects().length > 1;
    })).toBe(true);
    await titleLine.hover();
    await page.waitForTimeout(700);
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await expectWholeAndApart(dialog, dialog.locator(":scope > h2, :scope > p, :scope > ul, :scope > [data-slot=end-dialog-actions]"));
    await expectTextUncut(dialog.locator("h2, #end-macro-task-body, li, button"));
    await expectNoSidewaysScroll(page);
  });
});
