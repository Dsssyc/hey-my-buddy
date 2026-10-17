import { expect, test, type Locator, type Page } from "@playwright/test";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, settle } from "./preview";

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
  await page.locator(`[data-macro-task-id="${MACRO}"] button`).click();
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
        await expectReadableStart(label.locator(":scope > span:last-child > span"), state);
        await expectWholeAndApart(label, label.locator(":scope > span"));
      }

      await row(page, LEAD).focus();
      await expect(page.locator("[data-slot=timeline-status]")).toContainText(ownedState);
      await row(page, WAITING).focus();
      await row(page, WAITING).press("Enter");
      // Enter opens the micro task on the decision it waits for: the Lead's, with the request as recorded.
      const detail = page.locator("[data-slot=task-detail]");
      await expect(detail).toBeVisible();
      await expect(page.locator("[data-slot=detail-rail] [aria-current=step]")).toContainText("Lead");
      const request = detail.locator("[data-slot=detail-pane]");
      await expect(request).toContainText("需要 Lead 决定路径样例的核对范围");
      await expect(request).toContainText("由 Lead 确定后续核对范围");
      await expectTextUncut(request.locator("h3, dt, dd, p"));
      await expectNoSidewaysScroll(page);
      expect(errors).toEqual({ page: [], console: [] });

    });

    test("confirms the Lead's macro task stop scope and keeps the real read-only refusal honest", async ({ page }) => {
      const errors = observeErrors(page);
      const commands: unknown[] = [];
      page.on("request", request => {
        if (request.method() === "POST" && new URL(request.url()).pathname === "/api/command"
          && request.postDataJSON().operation === "macro_task_stop") commands.push(request.postDataJSON());
      });
      const { timeline, body } = await openLeadMacro(page, language, tasks);
      const zh = language === "zh-CN";
      const stopName = zh ? "停止宏任务" : "Stop macro task";
      const cancelName = zh ? "取消" : "Cancel";
      const confirmName = zh ? "确认停止宏任务" : "Confirm stop";
      const more = page.getByRole("button", { name: zh ? "更多" : "More", exact: true });
      await more.click();
      const stop = page.getByRole("button", { name: stopName, exact: true });
      await expect(stop).toBeEnabled();
      await stop.click();
      const dialog = page.getByRole("dialog", { name: stopName, exact: true });
      const summary = timeline.macroTask;
      await expect(dialog.locator("#stop-macro-task-name")).toHaveAttribute("aria-description", summary.title);
      await expect(dialog.locator("#stop-macro-task-name")).toHaveText(summary.title);
      await expect(dialog.locator("#stop-macro-task-counts li")).toHaveCount(5);
      await expect(dialog.locator("#stop-macro-task-body")).not.toContainText(summary.title);
      await expect(dialog.locator("#stop-macro-task-counts")).toContainText(zh
        ? `停止 ${summary.counts.stoppableRoots} 个微任务`
        : `Micro tasks to stop ${summary.counts.stoppableRoots}`);
      await expect(dialog.locator("#stop-macro-task-counts")).toContainText(zh
        ? `停止 ${summary.counts.stoppableHelpers} 个协助任务`
        : `Helper tasks to stop ${summary.counts.stoppableHelpers}`);
      await expect(dialog.getByRole("button", { name: cancelName, exact: true })).toBeFocused();
      await settle(page);
      await expectWholeAndApart(page.locator("body"), dialog);
      await expectWholeAndApart(dialog, dialog.locator(":scope > h2, :scope > p, :scope > ul, :scope > [data-slot=stop-dialog-actions]"));
      await expectWholeAndApart(dialog.locator("[data-slot=stop-dialog-actions]"), dialog.getByRole("button"));
      await expectTextUncut(dialog.locator("h2, #stop-macro-task-body, li, button"));
      await dialog.press("Escape");
      await expect(dialog).toBeHidden();
      expect(commands).toEqual([]);

      await more.click();
      await stop.click();
      await dialog.getByRole("button", { name: cancelName, exact: true }).press("Tab");
      const confirm = dialog.getByRole("button", { name: confirmName, exact: true });
      await expect(confirm).toBeFocused();
      const refusal = page.waitForResponse(response => response.request().method() === "POST"
        && new URL(response.url()).pathname === "/api/command"
        && response.request().postDataJSON().operation === "macro_task_stop");
      await confirm.press("Enter");
      const response = await refusal;
      expect(response.status()).toBe(403);
      expect(response.headers()["x-hey-my-buddy-preview"]).toBe("synthetic-fixture-data");
      expect(await response.json()).toMatchObject({ ok: false, error: { code: "CONSOLE_READ_ONLY" } });
      expect(commands).toEqual([{ operation: "macro_task_stop", params: { macroTaskId: MACRO, commandId: expect.any(String) } }]);
      expect(response.request().postDataJSON().params.commandId).not.toBe("");
      await expect(dialog).toBeHidden();
      const status = page.locator("[data-slot=macro-stop-state]");
      await expect(status).toHaveText(zh ? "停止请求未提交" : "Stop request was refused");
      await expect(status).toHaveAttribute("aria-description", /This synthetic preview never performs mutations/);
      await expectTextUncut(status);
      await expect(page.getByText(zh ? "已停止" : "Stopped", { exact: true })).toHaveCount(0);
      await expect(page.getByRole("button", { name: zh ? "重试停止" : "Retry stop", exact: true })).toHaveCount(0);

      const unchanged = await page.request.get(TIMELINE);
      expect(unchanged.status()).toBe(200);
      expect(unchanged.headers()["x-hey-my-buddy-preview"]).toBe("synthetic-fixture-data");
      expect(await unchanged.text()).toBe(body);
      await expectReadableStart(row(page, LEAD).locator(":scope > span:last-child > span"), zh ? "等待名下的微任务" : "Waiting for owned micro tasks");
      await expectReadableStart(row(page, WAITING).locator(":scope > span:last-child > span"), zh ? "等待 Lead" : "Waiting for Lead");
      await page.getByRole("button", { name: zh ? "知道了" : "Dismiss", exact: true }).click();
      await expect(status).toHaveCount(0);
      await more.click();
      await expect(stop).toBeEnabled();
      await expectNoSidewaysScroll(page);
      expect(errors.page).toEqual([]);
      // Chromium may log the actual 403 as a resource error. The response above
      // proves that this one expected refusal is from the read-only preview.
      expect(errors.console.length).toBeLessThanOrEqual(1);
      for (const message of errors.console) expect(message).toMatch(/^Failed to load resource:.*403/);
    });
  });
}

for (const locale of ["zh-CN", "en-US"]) test.describe(`a long stop title in ${locale} at narrow width`, () => {
  test.use({ locale, viewport: { width: 390, height: 844 } });
  test("truncates only the title while keeping the scope and controls readable", async ({ page }) => {
    const title = "核对完整范围与全部已交付结果的宏任务标题：".repeat(10);
    await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
      const response = await route.fetch();
      const data = await response.json();
      const target = data.macroTasks.find((item: { macroTaskId: string }) => item.macroTaskId === MACRO);
      target.title = title;
      await route.fulfill({ response, json: data });
    });
    await open(page, "#tasks");
    await page.locator(`[data-macro-task-id="${MACRO}"] button`).click();
    await page.getByRole("button", { name: locale === "zh-CN" ? "更多" : "More", exact: true }).click();
    await page.getByRole("button", { name: locale === "zh-CN" ? "停止宏任务" : "Stop macro task", exact: true }).click();
    const dialog = page.getByRole("dialog");
    const titleLine = dialog.locator("#stop-macro-task-name");
    await settle(page);
    await expect(titleLine).toHaveAttribute("aria-description", title);
    await expect(titleLine).toHaveText(title);
    await expect(dialog).toHaveAccessibleDescription(new RegExp(title.slice(0, 24)));
    expect(await titleLine.evaluate(element => {
      const style = getComputedStyle(element);
      return style.whiteSpace === "nowrap" && element.scrollWidth > element.clientWidth;
    })).toBe(true);
    await expectWholeAndApart(dialog, dialog.locator(":scope > h2, :scope > p, :scope > ul, :scope > [data-slot=stop-dialog-actions]"));
    await expectTextUncut(dialog.locator("h2, #stop-macro-task-body, li, button"));
    await expectNoSidewaysScroll(page);
  });
});
