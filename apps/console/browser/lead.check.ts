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
  return page.locator(`.tl-label[data-key="row:${runId}"]`);
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

// Task views still carry their existing Chinese copy. Both actual browser
// languages must render the same Lead facts, alongside their translated shell.
for (const { locale, language, tasks } of [
  { locale: "zh-CN", language: "zh-CN", tasks: "任务" },
  { locale: "en-US", language: "en", tasks: "Tasks" },
]) for (const width of [1440, 1024]) {
  test.describe(`Lead in the real synthetic preview in ${locale} at ${width}px`, () => {
    test.use({ locale, viewport: { width, height: 1000 } });

    test("keeps both waiting states readable and opens their recorded facts with the keyboard", async ({ page }) => {
      const errors = observeErrors(page);
      const { timeline } = await openLeadMacro(page, language, tasks);
      expect(timeline.rows.find(item => item.runId === LEAD)?.state).toBe("waiting-owned");
      expect(timeline.rows.find(item => item.runId === WAITING)?.leadOwnership).toMatchObject({
        leadRunId: LEAD, leadTurnId: "turn-att-b3-1",
      });
      expect(timeline.rows.find(item => item.runId === QUEUED)?.status).toBe("queued");
      for (const [runId, state] of [[LEAD, "等待名下的微任务"], [WAITING, "等待 Lead"]]) {
        const label = row(page, runId);
        await expect(label).toHaveAccessibleName(new RegExp(state));
        await expectReadableStart(label.locator(".lbl-sub"), state);
        await expectWholeAndApart(label, label.locator(":scope > span"));
      }

      const wait = row(page, WAITING).locator("..").locator(".sp.wait");
      await expect(wait).toHaveAccessibleName(/等待 Lead/);
      await expectReadableStart(wait.locator(".sp-text"), "等待 Lead");
      await wait.focus();
      await expect(wait).toBeFocused();
      await wait.press(" ");
      const expand = page.getByRole("button", { name: "展开检查器", exact: true });
      await expand.focus();
      await expand.press("Enter");
      await expect(page.locator(".inspector-dock-toggle")).toHaveAttribute("aria-expanded", "true");
      const card = page.locator(".inspector-card");
      await expect(card.locator(".inspector-field").filter({ hasText: "类型" }).locator("dd")).toHaveText("等待 Lead");
      const waitHeading = await card.locator(".inspector-card-head > strong").textContent();
      await settle(page);
      await expectWholeAndApart(card, card.locator(":scope > div, :scope > dl"));
      await expectTextUncut(card.locator("dt, dd"));

      await row(page, LEAD).focus();
      await row(page, LEAD).press(" ");
      await expectReadableStart(card.locator(".inspector-field").filter({ hasText: "结果" }).locator("dd"), "等待名下的微任务");
      await row(page, WAITING).focus();
      await row(page, WAITING).press("Enter");
      const request = page.locator(".attention-bar");
      await expect(request).toContainText("等待 Lead 决定：需要 Lead 决定路径样例的核对范围");
      await expectWholeAndApart(request, request.locator(":scope > span, :scope > button"));
      await expectTextUncut(request.locator("span, button"));
      await request.getByRole("button", { name: "查看请求", exact: true }).click();
      const assistance = page.getByRole("tabpanel", { name: "协作与待办", exact: true });
      await expect(assistance.getByText("等待 Lead 决定", { exact: true })).toBeVisible();
      await expectNoSidewaysScroll(page);
      expect(errors).toEqual({ page: [], console: [] });
      expect(waitHeading, "the inspector heading must identify the same Lead wait as its type field").toBe("等待 Lead");
    });

    test("confirms the Lead's macro task stop scope and keeps the real read-only refusal honest", async ({ page }) => {
      const errors = observeErrors(page);
      const commands: unknown[] = [];
      page.on("request", request => {
        if (request.method() === "POST" && new URL(request.url()).pathname === "/api/command"
          && request.postDataJSON().operation === "macro_task_stop") commands.push(request.postDataJSON());
      });
      const { timeline, body } = await openLeadMacro(page, language, tasks);
      const stop = page.getByRole("button", { name: "停止宏任务", exact: true });
      await expect(stop).toBeEnabled();
      await stop.click();
      const dialog = page.getByRole("dialog", { name: "停止宏任务", exact: true });
      const summary = timeline.macroTask;
      await expect(dialog).toHaveAccessibleDescription(
        `将停止「${summary.title}」中尚未验收的 ${summary.counts.roots - summary.counts.accepted} 个微任务及其协助任务；已验收的 ${summary.counts.accepted} 个保留。（涉及 ${summary.counts.helpers} 个协助任务）`,
      );
      await expect(dialog.getByRole("button", { name: "取消", exact: true })).toBeFocused();
      await expectWholeAndApart(page.locator("body"), dialog);
      await expectWholeAndApart(dialog, dialog.locator(":scope > h2, :scope > p, :scope > .actions"));
      await expectWholeAndApart(dialog.locator(".actions"), dialog.getByRole("button"));
      await expectTextUncut(dialog.locator("h2, p, button"));
      await dialog.press("Escape");
      await expect(dialog).toBeHidden();
      expect(commands).toEqual([]);

      await stop.click();
      await dialog.getByRole("button", { name: "取消", exact: true }).press("Tab");
      const confirm = dialog.getByRole("button", { name: "确认停止宏任务", exact: true });
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
      const status = page.locator(".stop-status");
      await expect(status).toHaveText("停止请求未提交");
      await expect(status).toHaveAttribute("title", /This synthetic preview never performs mutations/);
      await expectTextUncut(status);
      await expect(page.getByText("已停止", { exact: true })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "重试停止", exact: true })).toHaveCount(0);

      const unchanged = await page.request.get(TIMELINE);
      expect(unchanged.status()).toBe(200);
      expect(unchanged.headers()["x-hey-my-buddy-preview"]).toBe("synthetic-fixture-data");
      expect(await unchanged.text()).toBe(body);
      await expectReadableStart(row(page, LEAD).locator(".lbl-sub"), "等待名下的微任务");
      await expectReadableStart(row(page, WAITING).locator(".lbl-sub"), "等待 Lead");
      await page.getByRole("button", { name: "知道了", exact: true }).click();
      await expect(status).toHaveCount(0);
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
