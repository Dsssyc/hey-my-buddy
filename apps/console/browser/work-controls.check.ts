import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expectNoSidewaysScroll, open } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";
const STANDALONE = "run:preview-run-c1";
const PAUSED_AT = "2026-09-27T10:00:00.000Z";
const shots = fileURLToPath(new URL("../../../tmp/pause-console/", import.meta.url));

const caseIds = new WeakMap<Page, string>();
test.beforeEach(async ({ page }) => {
  const id = randomUUID();
  caseIds.set(page, id);
  await page.context().setExtraHTTPHeaders({ "X-HMB-Preview-Case": id });
});
const caseHeaders = (page: Page) => ({ "X-HMB-Preview-Case": caseIds.get(page)! });

type Command = { operation: string; params: { macroTaskId: string; commandId: string } };
const more = (page: Page, id = A) => page.locator(`[data-macro-task-id="${id}"] [data-slot=macro-menu-button]`);
const openMenu = async (page: Page, id = A) => { await entry(page, id).hover(); await more(page, id).click(); };
const count = (page: Page) => page.locator("[data-slot=macro-overview-count-paused]");
const entry = (page: Page, id = A) => page.locator(`[data-macro-task-id="${id}"]`);
const commandLog = (page: Page, operation: string) => {
  const commands: Command[] = [];
  page.on("request", request => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/command"
      && request.postDataJSON().operation === operation) commands.push(request.postDataJSON());
  });
  return commands;
};

async function pausedViews(page: Page, proofs: Record<string, boolean>, activeCounts: Record<string, number> = {}) {
  await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
    const response = await route.fetch();
    const data = await response.json();
    const rows = data.groups.flatMap((group: { macroTasks: { macroTaskId: string }[] }) => group.macroTasks);
    const first = rows.find((row: { macroTaskId: string }) => row.macroTaskId === A);
    for (const row of rows) {
      if (Object.hasOwn(proofs, row.macroTaskId)) {
        row.paused = { pausedAt: PAUSED_AT, shutdownConfirmed: proofs[row.macroTaskId] };
        if (row.macroTaskId === B) row.counts = { ...first.counts };
        if (Object.hasOwn(activeCounts, row.macroTaskId)) row.counts.active = activeCounts[row.macroTaskId];
      }
    }
    await route.fulfill({ response, json: data });
  });
  for (const id of Object.keys(proofs)) {
    await page.route(new RegExp(`/api/macro-tasks/${id}/timeline(?:\\?|$)`), async route => {
      const response = await route.fetch();
      const data = await response.json();
      data.macroTask.paused = { pausedAt: PAUSED_AT, shutdownConfirmed: proofs[id] };
      if (Object.hasOwn(activeCounts, id)) data.macroTask.counts.active = activeCounts[id];
      await route.fulfill({ response, json: data });
    });
  }
}

for (const locale of ["zh-CN", "en-US"] as const) {
  test.describe(`macro work controls in ${locale}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 960 }, reducedMotion: "reduce" });
    test.beforeEach(async () => { await mkdir(shots, { recursive: true }); });
    test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: "wait" }); });
    const zh = locale === "zh-CN";

    test("sends pause and resume immediately with distinct identities and receipt-only replies", async ({ page }) => {
      const pauses = commandLog(page, "macro_task_pause");
      const resumes = commandLog(page, "macro_task_resume");
      await open(page, `#tasks/${A}`);
      await openMenu(page);
      const pauseResponse = page.waitForResponse(response => response.request().postDataJSON()?.operation === "macro_task_pause");
      await page.getByRole("menuitem", { name: zh ? "暂停" : "Pause", exact: true }).click();
      expect((await (await pauseResponse).json()).result).toEqual({ macroTaskId: A, duplicate: false });
      await expect(page.getByRole("dialog")).toHaveCount(0);
      expect(pauses).toEqual([{ operation: "macro_task_pause", params: { macroTaskId: A, commandId: expect.any(String) } }]);
      await expect(count(page)).toContainText(zh ? "暂停中" : "Pausing");
      await openMenu(page);
      const resumeResponse = page.waitForResponse(response => response.request().postDataJSON()?.operation === "macro_task_resume");
      await page.getByRole("menuitem", { name: zh ? "继续" : "Resume", exact: true }).click();
      expect((await (await resumeResponse).json()).result).toEqual({ macroTaskId: A, duplicate: false });
      await expect(page.getByRole("dialog")).toHaveCount(0);
      expect(resumes).toEqual([{ operation: "macro_task_resume", params: { macroTaskId: A, commandId: expect.any(String) } }]);
      expect(resumes[0]!.params.commandId).not.toBe(pauses[0]!.params.commandId);
      await expect(count(page)).toHaveCount(0);
    });

    test("shows pause proof in the count and gives its mark priority in the list", async ({ page }) => {
      await pausedViews(page, { [A]: false, [B]: true });
      await open(page, `#tasks/${A}`);
      await expect(count(page)).toContainText(zh ? "暂停中" : "Pausing");
      await expect(count(page).locator("dd")).toHaveText("2");
      await expect(count(page).locator('[data-slot=state-mark]')).toHaveAttribute("data-kind", "paused");
      await expect(count(page).locator('[data-slot=state-mark]')).toHaveClass(/state-breathe/);
      await expect(entry(page, A).locator('[data-slot=state-mark]')).toHaveAttribute("data-kind", "paused");
      await expect(entry(page, A).locator('[data-slot=state-mark]')).toHaveClass(/state-breathe/);
      await expect(entry(page, A).locator(".sr-only")).toHaveText(zh ? "暂停中" : "Pausing");
      await expect(entry(page, B).locator('[data-slot=state-mark]')).toHaveAttribute("data-kind", "paused");
      await page.locator(`[data-macro-task-id="${B}"] [data-slot=macro-entry]`).click();
      await expect(count(page)).toContainText(zh ? "已暂停" : "Paused");
      await expect(count(page).locator('[data-slot=state-mark]')).not.toHaveClass(/state-breathe/);
      await expect(entry(page, B).locator('[data-slot=state-mark]')).not.toHaveClass(/state-breathe/);
      await expect(entry(page, B).locator(".sr-only")).toHaveText(zh ? "已暂停" : "Paused");
      await expect(page.locator("[data-slot=macro-pause-state]")).toHaveCount(0);
      await expect(page.getByText(zh ? /已暂停 ·/ : /Paused ·/)).toHaveCount(0);
    });

    test("gives the pause mark priority over Host, review, running and failure", async ({ page }) => {
      await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
        const response = await route.fetch();
        const data = await response.json();
        const row = data.groups.flatMap((group: { macroTasks: { macroTaskId: string }[] }) => group.macroTasks)
          .find((item: { macroTaskId: string }) => item.macroTaskId === B);
        expect(row.counts.host).toBeGreaterThan(0);
        expect(row.counts.review).toBeGreaterThan(0);
        expect(row.counts.active).toBeGreaterThan(0);
        expect(row.counts.failed).toBeGreaterThan(0);
        row.paused = { pausedAt: PAUSED_AT, shutdownConfirmed: false };
        await route.fulfill({ response, json: data });
      });
      await page.route(new RegExp(`/api/macro-tasks/${B}/timeline(?:\\?|$)`), async route => {
        const response = await route.fetch();
        const data = await response.json();
        data.macroTask.paused = { pausedAt: PAUSED_AT, shutdownConfirmed: false };
        await route.fulfill({ response, json: data });
      });
      await open(page, `#tasks/${B}`);
      await expect(entry(page, B).locator('[data-slot=state-mark]')).toHaveAttribute("data-kind", "paused");
      await expect(entry(page, B).locator(".sr-only")).toHaveText(zh ? "暂停中" : "Pausing");
      await expect(count(page).locator('[data-slot=state-mark]')).toHaveAttribute("data-kind", "paused");
    });

    test("offers the same controls for a standalone micro task", async ({ page }) => {
      // The fixture's accepted root has no unfinished work. Project one newly
      // unfinished micro task into this list read to exercise its End entry.
      await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
        const response = await route.fetch();
        const data = await response.json();
        const row = data.groups.flatMap((group: { macroTasks: { macroTaskId: string }[] }) => group.macroTasks)
          .find((item: { macroTaskId: string }) => item.macroTaskId === STANDALONE);
        row.counts.unfinished = 1;
        await route.fulfill({ response, json: data });
      });
      await open(page, "#tasks");
      await page.locator('[data-slot=macro-group][data-group-id="standalone"] [data-slot=macro-group-heading]').click();
      await openMenu(page, STANDALONE);
      await expect(page.getByRole("menuitem", { name: zh ? "暂停" : "Pause", exact: true })).toBeVisible();
      await expect(page.getByRole("menuitem", { name: zh ? "结束" : "End", exact: true })).toBeVisible();
      const command = commandLog(page, "macro_task_pause");
      const response = page.waitForResponse(reply => reply.request().postDataJSON()?.operation === "macro_task_pause");
      await page.getByRole("menuitem", { name: zh ? "暂停" : "Pause", exact: true }).click();
      expect((await (await response).json()).result).toEqual({ macroTaskId: STANDALONE, duplicate: false });
      expect(command[0]!.params.macroTaskId).toBe(STANDALONE);
      await expect(entry(page, STANDALONE).locator('[data-slot=state-mark]')).toHaveAttribute("data-kind", "paused");
      await openMenu(page, STANDALONE);
      await expect(page.getByRole("menuitem", { name: zh ? "继续" : "Resume", exact: true })).toBeVisible();
    });

    test("keeps end confirmation open and busy until its receipt then reads the view", async ({ page }) => {
      const commands = commandLog(page, "macro_task_end");
      const target = B;
      let release!: () => void;
      const held = new Promise<void>(resolve => { release = resolve; });
      let firstHeld = true;
      await page.route("**/api/command", async route => {
        if (route.request().postDataJSON().operation === "macro_task_end" && firstHeld) {
          firstHeld = false;
          await held;
        }
        await route.fallback();
      });
      await open(page, `#tasks/${B}`);
      const beforeResponse = await page.request.get(`/api/macro-tasks/${B}/timeline`, { headers: caseHeaders(page) });
      const before = await beforeResponse.json();
      await openMenu(page, B);
      await page.getByRole("menuitem", { name: zh ? "结束" : "End", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: zh ? "结束宏任务" : "End macro task" });
      await expect(dialog.locator("#end-macro-task-counts")).toContainText(zh ? `结束 ${before.macroTask.counts.unfinished} 个微任务` : `End ${before.macroTask.counts.unfinished} micro tasks`);
      const response = page.waitForResponse(reply => reply.request().postDataJSON()?.operation === "macro_task_end");
      await dialog.getByRole("button", { name: zh ? "确认结束" : "End", exact: true }).click();
      const confirm = dialog.getByRole("button", { name: zh ? "确认结束" : "End", exact: true });
      try {
        await expect(dialog).toBeVisible();
        await expect(confirm).toBeDisabled();
        await expect(confirm).toHaveAttribute("aria-busy", "true");
        await expect(confirm.locator('[data-slot=state-mark]')).toHaveAttribute("data-kind", "live");
        await expect(page.locator('[data-slot=macro-work-state]')).toHaveCount(0);
        await page.screenshot({ path: join(shots, `round4-end-busy-${locale}.png`) });
      } finally { release(); }
      expect((await (await response).json()).result).toEqual({ macroTaskId: target, duplicate: false });
      expect(commands).toEqual([{ operation: "macro_task_end", params: { macroTaskId: target, commandId: expect.any(String) } }]);
      await expect(dialog).toHaveCount(0);
      await expect(page.locator('[data-slot=macro-work-state]')).toHaveCount(0);
      const afterResponse = await page.request.get(`/api/macro-tasks/${B}/timeline`, { headers: caseHeaders(page) });
      const after = await afterResponse.json();
      expect(after.macroTask.counts.unfinished).toBeLessThan(before.macroTask.counts.unfinished);
      const concluded = after.rows.filter((row: { state: string }) => row.state === "concluded");
      expect(concluded.length).toBeGreaterThan(before.rows.filter((row: { state: string }) => row.state === "concluded").length);
      const ended = concluded.find((row: { runId: string }) => !before.rows.some((old: { runId: string; state: string }) => old.runId === row.runId && old.state === "concluded"));
      await expect(page.locator(`[data-slot=timeline-row-name][data-run-id="${ended.runId}"]`)).toHaveAccessibleName(new RegExp(zh ? "已结束" : "Ended"));
      await page.screenshot({ path: join(shots, `round4-end-view-${locale}.png`) });
      await openMenu(page, B);
      await page.getByRole("menuitem", { name: zh ? "结束" : "End", exact: true }).click();
      const again = page.getByRole("dialog", { name: zh ? "结束宏任务" : "End macro task" });
      const secondResponse = page.waitForResponse(reply => reply.request().postDataJSON()?.operation === "macro_task_end");
      await again.getByRole("button", { name: zh ? "确认结束" : "End", exact: true }).click();
      expect((await (await secondResponse).json()).result).toEqual({ macroTaskId: target, duplicate: false });
      expect(commands).toHaveLength(2);
      expect(commands[1]!.params.commandId).not.toBe(commands[0]!.params.commandId);
      await expect(page.locator('[data-slot=macro-work-state]')).toHaveCount(0);
    });

    test("replays an unknown receipt with one line and its original identity", async ({ page }) => {
      const commands = commandLog(page, "macro_task_end");
      let first = true;
      await page.route("**/api/command", async route => {
        if (route.request().postDataJSON().operation === "macro_task_end" && first) {
          first = false;
          const reply = await route.fetch();
          expect(reply.status()).toBe(200);
          await route.fulfill({ response: reply, json: { ok: true, result: { macroTaskId: A, results: "old-shape", duplicate: false } } });
        } else await route.fallback();
      });
      await open(page, `#tasks/${A}`);
      await openMenu(page);
      await page.getByRole("menuitem", { name: zh ? "结束" : "End", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: zh ? "结束宏任务" : "End macro task" });
      await dialog.getByRole("button", { name: zh ? "确认结束" : "End", exact: true }).click();
      const status = page.locator('[data-slot=macro-work-state][data-action=end]');
      await expect(status.locator("p")).toHaveCount(1);
      await expect(status).toContainText(zh ? "结束的结果未知" : "The end result is unknown");
      await expect(status.getByRole("button", { name: zh ? "结束" : "End", exact: true })).toBeVisible();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      const response = page.waitForResponse(reply => reply.request().postDataJSON()?.operation === "macro_task_end");
      await status.getByRole("button", { name: zh ? "结束" : "End", exact: true }).click();
      expect((await (await response).json()).result).toEqual({ macroTaskId: A, duplicate: true });
      expect(commands).toHaveLength(2);
      expect(commands[1]).toEqual(commands[0]);
      await expect(status).toHaveCount(0);
    });

    for (const action of ["pause", "resume", "end"] as const) test(`shows one ${action} refusal line without result controls`, async ({ page }) => {
      const operation = `macro_task_${action}`;
      if (action === "resume") await pausedViews(page, { [A]: true });
      await page.route("**/api/command", async route => {
        if (route.request().postDataJSON().operation === operation) {
          await route.fulfill({ status: 403, json: { ok: false, error: { code: "FORBIDDEN", message: "Synthetic refusal" } } });
        } else await route.fallback();
      });
      await open(page, `#tasks/${A}`);
      await openMenu(page);
      const name = action === "pause" ? zh ? "暂停" : "Pause" : action === "resume" ? zh ? "继续" : "Resume" : zh ? "结束" : "End";
      await page.getByRole("menuitem", { name, exact: true }).click();
      if (action === "end") {
        const dialog = page.getByRole("dialog", { name: zh ? "结束宏任务" : "End macro task" });
        await dialog.getByRole("button", { name: zh ? "确认结束" : "End", exact: true }).click();
        await expect(dialog).toHaveCount(0);
      }
      const status = page.locator(`[data-slot=macro-work-state][data-action=${action}]`);
      await expect(status).toHaveText(zh ? "无操作权限；请重新打开控制台。" : "Not permitted; reopen the console.");
      await expect(status.locator("p")).toHaveCount(1);
      await expect(status.getByRole("button")).toHaveCount(0);
      await expect(page.getByText("Synthetic refusal")).toHaveCount(0);
    });

    test("keeps the pause interruption marker and its recorded reason", async ({ page }) => {
      const id = "execution:att-b6-1";
      await page.route(new RegExp(`/api/macro-tasks/${B}/timeline(?:\\?|$)`), async route => {
        const response = await route.fetch();
        const data = await response.json();
        data.spans.find((item: { spanId: string }) => item.spanId === id).terminationReason = "pause";
        await route.fulfill({ response, json: data });
      });
      await page.route("**/api/tasks/preview-run-b6", async route => {
        const response = await route.fetch();
        const task = await response.json();
        task.terminationReason = "pause";
        if (task.selectedAttempt?.result) task.selectedAttempt.result.terminationReason = "pause";
        await route.fulfill({ response, json: task });
      });
      await open(page, `#tasks/${B}`);
      const marker = page.locator(`[data-slot=pause-end-mark][data-span-id="${id}"]`);
      await expect(marker).toHaveCount(1);
      const segment = page.locator(`[data-key="span:${id}"]`);
      await segment.focus();
      const cause = zh ? "用户在控制台暂停，回合已中断。" : "The user paused this work in the console; the turn was interrupted.";
      await expect(page.locator("[data-slot=timeline-status]")).toContainText(cause);
      await segment.press("Enter");
      await expect(page.locator("[data-slot=task-detail] [data-slot=detail-pane]")).toContainText(cause);
    });

    test("names a paused queue in the timeline row and detail header", async ({ page }) => {
      const runId = "preview-run-a3";
      await page.route(new RegExp(`/api/macro-tasks/${A}/timeline(?:\\?|$)`), async route => {
        const response = await route.fetch();
        const data = await response.json();
        const row = data.rows.find((item: { runId: string }) => item.runId === runId);
        row.status = "queued";
        row.queueReason = "paused";
        row.shutdownConfirmed = true;
        await route.fulfill({ response, json: data });
      });
      await page.route(`**/api/tasks/${runId}`, async route => {
        const response = await route.fetch();
        const task = await response.json();
        task.state = "queued";
        task.queueReason = "paused";
        await route.fulfill({ response, json: task });
      });
      await open(page, `#tasks/${A}`);
      const row = page.locator(`[data-slot=timeline-row-name][data-run-id="${runId}"]`);
      await expect(row).toHaveAccessibleName(new RegExp(zh ? "已暂停" : "Paused"));
      await expect(row.locator('[data-slot=state-mark]')).toHaveAttribute("data-kind", "paused");
      await expect(row).not.toContainText(zh ? "排队" : "Queued");
      await row.focus();
      await row.press("Enter");
      const header = page.locator("[data-slot=detail-status]");
      await expect(header).toHaveText(zh ? "已暂停" : "Paused");
      await expect(header.locator('[data-slot=state-mark]')).toHaveAttribute("data-kind", "paused");
      await expect(page.locator("[data-slot=detail-queue-reason]")).toHaveCount(0);
      await page.screenshot({ path: join(shots, `round4-paused-detail-${locale}.png`) });
    });
  });
}

for (const locale of ["zh-CN", "en-US"] as const) for (const theme of ["light", "dark"] as const) for (const scene of ["pausing", "paused", "empty"] as const) {
  test.describe(`pause mark in ${locale} ${theme} ${scene}`, () => {
    test.use({ locale, colorScheme: theme, viewport: { width: 1024, height: 768 }, reducedMotion: "reduce" });
    test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: "wait" }); });
    test("keeps the count and marks within the header", async ({ page }) => {
      await mkdir(shots, { recursive: true });
      const confirmed = scene !== "pausing";
      await pausedViews(page, { [A]: confirmed }, scene === "empty" ? { [A]: 0 } : {});
      await open(page, `#tasks/${A}`);
      const header = page.locator("[data-slot=macro-overview-header]");
      const status = count(page);
      const marker = status.locator('[data-slot=state-mark]');
      await expect(status).toContainText(locale === "zh-CN" ? confirmed ? "已暂停" : "暂停中" : confirmed ? "Paused" : "Pausing");
      await expect(marker).toHaveAttribute("data-kind", "paused");
      if (scene === "empty") await expect(status.locator("dd")).toHaveCount(0);
      else await expect(status.locator("dd")).toHaveText("2");
      if (scene === "pausing") await expect(marker).toHaveClass(/state-breathe/);
      else await expect(marker).not.toHaveClass(/state-breathe/);
      await expect(entry(page).locator('[data-slot=state-mark]')).toHaveAttribute("data-kind", "paused");
      await status.hover();
      await expect(page.getByRole("tooltip")).toHaveCount(0);
      await expect(header.locator("[data-slot=macro-menu-button]")).toHaveCount(0);
      const boxes = await header.evaluate(element => {
        const frame = element.getBoundingClientRect();
        return ["macro-overview-counts"].map(slot => {
          const node = element.querySelector(`[data-slot="${slot}"]`)!;
          const box = node.getBoundingClientRect();
          return { left: box.left, top: box.top, right: box.right, bottom: box.bottom,
            frameLeft: frame.left, frameTop: frame.top, frameRight: frame.right, frameBottom: frame.bottom };
        });
      });
      for (const box of boxes) {
        expect(box.left).toBeGreaterThanOrEqual(box.frameLeft - 1);
        expect(box.top).toBeGreaterThanOrEqual(box.frameTop - 1);
        expect(box.right).toBeLessThanOrEqual(box.frameRight + 1);
        expect(box.bottom).toBeLessThanOrEqual(box.frameBottom + 1);
      }
      await expectNoSidewaysScroll(page);
      await page.screenshot({ path: join(shots, `round4-pause-${locale}-${theme}-${scene}.png`) });
    });
  });
}

for (const { locale, theme, confirmed } of [
  { locale: "zh-CN", theme: "light", confirmed: false },
  { locale: "en-US", theme: "dark", confirmed: true },
] as const) {
  test.describe(`wide pause layout in ${locale} ${theme}`, () => {
    test.use({ locale, colorScheme: theme, viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
    test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: "wait" }); });
    test("keeps the paused count with the other counts", async ({ page }) => {
      await mkdir(shots, { recursive: true });
      await pausedViews(page, { [A]: confirmed });
      await open(page, `#tasks/${A}`);
      const count = page.locator("[data-slot=macro-overview-count-paused]");
      await expect(count).toContainText(locale === "zh-CN" ? confirmed ? "已暂停" : "暂停中" : confirmed ? "Paused" : "Pausing");
      await expect(count.locator('[data-slot=state-mark]')).toHaveAttribute("data-kind", "paused");
      await expect(page.locator("[data-slot=macro-pause-state]")).toHaveCount(0);
      await expect(entry(page)).toBeVisible();
      await expect(page.locator("[data-slot=macro-list-total]")).toHaveText("14");
      await expectNoSidewaysScroll(page);
      await page.screenshot({ path: join(shots, `round4-pause-wide-${locale}-${theme}.png`) });
    });

    test("aligns a zero-count paused label with the other count labels", async ({ page }) => {
      await mkdir(shots, { recursive: true });
      await pausedViews(page, { [A]: true }, { [A]: 0 });
      await open(page, `#tasks/${A}`);
      await expect(entry(page)).toBeVisible();
      await expect(page.locator("[data-slot=macro-list-total]")).toHaveText("14");
      const status = page.locator("[data-slot=macro-overview-count-paused]");
      await expect(status).toContainText(locale === "zh-CN" ? "已暂停" : "Paused");
      await expect(status.locator("dd")).toHaveCount(0);
      const bottoms = await page.locator("[data-slot=macro-overview-counts]").evaluate(element => {
        const bottom = (slot: string) => element.querySelector(`[data-slot="macro-overview-count-${slot}"] dt`)!.getBoundingClientRect().bottom;
        return [bottom("paused"), bottom("accepted"), bottom("review")];
      });
      expect(Math.abs(bottoms[0]! - bottoms[1]!)).toBeLessThanOrEqual(1);
      expect(Math.abs(bottoms[0]! - bottoms[2]!)).toBeLessThanOrEqual(1);
      await expectNoSidewaysScroll(page);
      await page.screenshot({ path: join(shots, `round4-pause-wide-${locale}-${theme}-empty.png`) });
    });
  });
}
