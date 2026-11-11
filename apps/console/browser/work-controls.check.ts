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

type Command = { operation: string; params: { macroTaskId: string; commandId: string } };
const more = (page: Page, id = A) => page.locator(`[data-macro-task-id="${id}"] [data-slot=macro-menu-button]`);

async function pausedViews(page: Page, proofs: Record<string, boolean>) {
  await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
    const response = await route.fetch();
    const data = await response.json();
    const rows = data.groups.flatMap((group: { macroTasks: { macroTaskId: string }[] }) => group.macroTasks);
    const first = rows.find((row: { macroTaskId: string }) => row.macroTaskId === A);
    for (const row of rows) {
      if (Object.hasOwn(proofs, row.macroTaskId)) {
        row.paused = { pausedAt: PAUSED_AT, shutdownConfirmed: proofs[row.macroTaskId] };
        if (row.macroTaskId === B) row.counts = { ...first.counts };
      }
    }
    await route.fulfill({ response, json: data });
  });
  for (const id of Object.keys(proofs)) {
    await page.route(new RegExp(`/api/macro-tasks/${id}/timeline(?:\\?|$)`), async route => {
      const response = await route.fetch();
      const data = await response.json();
      data.macroTask.paused = { pausedAt: PAUSED_AT, shutdownConfirmed: proofs[id] };
      await route.fulfill({ response, json: data });
    });
  }
}

for (const locale of ["zh-CN", "en-US"] as const) {
  test.describe(`macro work controls in ${locale}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 960 }, reducedMotion: "reduce" });
    test.beforeEach(async () => { await mkdir(shots, { recursive: true }); });
    // Finish any routed polling read before releasing the page fixture.
    test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: "wait" }); });
    const zh = locale === "zh-CN";

    test("pauses and resumes immediately with distinct command identities", async ({ page }) => {
      const commands: Command[] = [];
      page.on("request", request => {
        if (request.method() === "POST" && new URL(request.url()).pathname === "/api/command"
          && /^macro_task_(pause|resume)$/.test(request.postDataJSON().operation)) commands.push(request.postDataJSON());
      });
      await open(page, `#tasks/${A}`);
      await more(page).click();
      const pause = page.getByRole("menuitem", { name: zh ? "暂停" : "Pause", exact: true });
      await expect(pause).toBeEnabled();
      const pauseResponse = page.waitForResponse(response => response.request().postDataJSON()?.operation === "macro_task_pause");
      await pause.click();
      expect((await (await pauseResponse).json()).result).toEqual({ macroTaskId: A, pausedAt: PAUSED_AT, duplicate: false });
      await expect(page.getByRole("dialog")).toHaveCount(0);
      expect(commands).toEqual([{ operation: "macro_task_pause", params: { macroTaskId: A, commandId: expect.any(String) } }]);
      expect(commands[0]!.params.commandId).not.toBe("");

      await pausedViews(page, { [A]: true });
      await page.reload();
      await more(page).click();
      const resume = page.getByRole("menuitem", { name: zh ? "继续" : "Resume", exact: true });
      await expect(resume).toBeEnabled();
      const resumeResponse = page.waitForResponse(response => response.request().postDataJSON()?.operation === "macro_task_resume");
      await resume.click();
      expect((await (await resumeResponse).json()).result).toEqual({ macroTaskId: A, duplicate: false });
      await expect(page.getByRole("dialog")).toHaveCount(0);
      expect(commands).toEqual([
        { operation: "macro_task_pause", params: { macroTaskId: A, commandId: expect.any(String) } },
        { operation: "macro_task_resume", params: { macroTaskId: A, commandId: expect.any(String) } },
      ]);
      expect(commands[1]!.params.commandId).not.toBe(commands[0]!.params.commandId);
    });

    test("uses view stop proof for list and header pause states", async ({ page }) => {
      await pausedViews(page, { [A]: false, [B]: true });
      await open(page, `#tasks/${A}`);
      await expect(page.locator(`[data-macro-task-id="${A}"] [data-slot=macro-pause-state]`)).toHaveText(zh ? "暂停中" : "Pausing");
      await expect(page.locator("[data-slot=macro-overview-header] [data-slot=macro-pause-state]")).toHaveText(zh ? "暂停中" : "Pausing");
      await expect(page.locator(`[data-macro-task-id="${B}"] [data-slot=macro-pause-state]`)).toHaveText(zh ? /已暂停 · \d{1,2}:\d{2}/ : /Paused · \d{1,2}:\d{2}/);
      await page.screenshot({ path: join(shots, `pause-state-${locale}.png`) });
      await page.locator(`[data-macro-task-id="${B}"] [data-slot=macro-entry]`).click();
      await expect(page.locator("[data-slot=macro-overview-header] [data-slot=macro-pause-state]")).toHaveText(zh ? /已暂停 · \d{1,2}:\d{2}/ : /Paused · \d{1,2}:\d{2}/);
    });

    test("offers the same actions for a standalone micro task", async ({ page }) => {
      let paused = false;
      await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
        const response = await route.fetch();
        const data = await response.json();
        const row = data.groups.flatMap((group: { macroTasks: { macroTaskId: string }[] }) => group.macroTasks)
          .find((item: { macroTaskId: string }) => item.macroTaskId === STANDALONE);
        row.counts.unfinished = 1;
        row.paused = paused ? { pausedAt: PAUSED_AT, shutdownConfirmed: true } : null;
        await route.fulfill({ response, json: data });
      });
      const openStandalone = async () => {
        await open(page, "#tasks");
        await page.locator('[data-slot=macro-group][data-group-id="standalone"] [data-slot=macro-group-heading]').click();
        await page.locator(`[data-macro-task-id="${STANDALONE}"]`).hover();
        await more(page, STANDALONE).click();
      };
      await openStandalone();
      await expect(page.getByRole("menuitem", { name: zh ? "暂停" : "Pause", exact: true })).toBeVisible();
      await expect(page.getByRole("menuitem", { name: zh ? "结束" : "End", exact: true })).toBeVisible();
      const pauseResponse = page.waitForResponse(response => response.request().postDataJSON()?.operation === "macro_task_pause");
      await page.getByRole("menuitem", { name: zh ? "暂停" : "Pause", exact: true }).click();
      expect((await pauseResponse).request().postDataJSON().params).toEqual({ macroTaskId: STANDALONE, commandId: expect.any(String) });
      await expect(page.getByRole("dialog")).toHaveCount(0);
      paused = true;
      await page.reload();
      await page.locator('[data-slot=macro-group][data-group-id="standalone"] [data-slot=macro-group-heading]').click();
      await page.locator(`[data-macro-task-id="${STANDALONE}"]`).hover();
      await more(page, STANDALONE).click();
      await expect(page.getByRole("menuitem", { name: zh ? "继续" : "Resume", exact: true })).toBeVisible();
      await expect(page.getByRole("menuitem", { name: zh ? "结束" : "End", exact: true })).toBeVisible();
    });

    test("starts a new end after a known partial result and confirms the current scope", async ({ page }) => {
      const commands: Command[] = [];
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
      page.on("request", request => {
        if (request.method() === "POST" && new URL(request.url()).pathname === "/api/command"
          && request.postDataJSON().operation === "macro_task_end") commands.push(request.postDataJSON());
      });
      await open(page, `#tasks/${A}`);
      await more(page).click();
      await page.getByRole("menuitem", { name: zh ? "结束" : "End", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: zh ? "结束宏任务" : "End macro task" });
      const firstResponse = page.waitForResponse(response => response.request().postDataJSON()?.operation === "macro_task_end");
      await dialog.getByRole("button", { name: zh ? "确认结束" : "End", exact: true }).click();
      try {
        await expect(page.locator('[data-slot=macro-work-state][data-action=end]')).toContainText(zh ? "结束中" : "Ending");
        await page.screenshot({ path: join(shots, `end-progress-${locale}.png`) });
      } finally { release(); }
      const first = (await (await firstResponse).json()).result;
      expect(first.results).toHaveLength(4);
      const ended = first.results.filter((item: { concluded: boolean }) => item.concluded);
      const unconfirmed = first.results.filter((item: { concluded: boolean }) => !item.concluded);
      expect(ended).toHaveLength(2);
      expect(unconfirmed).toHaveLength(2);
      for (const item of unconfirmed) expect(item.error.code).toBe("SHUTDOWN_UNCONFIRMED");
      await expect(page.locator('[data-slot=macro-work-state][data-action=end]')).toContainText(zh
        ? "2 个微任务的停止未确认，没有写结论，可以重试"
        : "2 micro tasks are not confirmed stopped, so no conclusion was recorded; you can retry");
      await page.screenshot({ path: join(shots, `end-partial-${locale}.png`) });
      const retry = page.getByRole("button", { name: zh ? "重试结束" : "Retry end" });
      await retry.click();
      const secondDialog = page.getByRole("dialog", { name: zh ? "结束宏任务" : "End macro task" });
      await expect(secondDialog).toBeVisible();
      await expect(secondDialog.locator("#end-macro-task-counts")).toContainText(zh ? "结束 4 个微任务" : "End 4 micro tasks");
      await expect(secondDialog.locator("#end-macro-task-counts")).toContainText(zh ? "保留 1 个已交付微任务" : "Keep 1 delivered micro tasks");
      expect(commands).toHaveLength(1);
      const secondResponse = page.waitForResponse(response => response.request().postDataJSON()?.operation === "macro_task_end");
      await secondDialog.getByRole("button", { name: zh ? "确认结束" : "End", exact: true }).click();
      const second = (await (await secondResponse).json()).result;
      expect(second).toEqual({ ...first, duplicate: false });
      expect(commands).toHaveLength(2);
      expect(commands[1]!.params.commandId).not.toBe(commands[0]!.params.commandId);
      await expect(secondDialog).toHaveCount(0);
    });

    test("renders every real end outcome and confirms the refreshed scope", async ({ page }) => {
      const commands: Command[] = [];
      let afterFirstEnd = false;
      let releaseRead!: () => void;
      const readGate = new Promise<void>(resolve => { releaseRead = resolve; });
      await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
        const response = await route.fetch();
        const data = await response.json();
        if (afterFirstEnd) {
          await readGate;
          const row = data.groups.flatMap((group: { macroTasks: { macroTaskId: string }[] }) => group.macroTasks)
            .find((item: { macroTaskId: string }) => item.macroTaskId === B);
          row.counts = { ...row.counts, unfinished: 2, delivered: 2 };
        }
        await route.fulfill({ response, json: data });
      });
      await page.route("**/api/command", async route => {
        if (route.request().postDataJSON().operation === "macro_task_end") afterFirstEnd = true;
        await route.fallback();
      });
      page.on("request", request => {
        if (request.method() === "POST" && new URL(request.url()).pathname === "/api/command"
          && request.postDataJSON().operation === "macro_task_end") commands.push(request.postDataJSON());
      });
      await open(page, `#tasks/${B}`);
      await more(page, B).click();
      await page.getByRole("menuitem", { name: zh ? "结束" : "End", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: zh ? "结束宏任务" : "End macro task" });
      await expect(dialog.locator("#end-macro-task-counts")).toContainText(zh ? "结束 6 个微任务" : "End 6 micro tasks");
      const firstResponse = page.waitForResponse(response => response.request().postDataJSON()?.operation === "macro_task_end");
      await dialog.getByRole("button", { name: zh ? "确认结束" : "End", exact: true }).click();
      const first = (await (await firstResponse).json()).result;
      expect(first.results).toHaveLength(6);
      const status = page.locator('[data-slot=macro-work-state][data-action=end]');
      await expect(status.locator('[data-slot=macro-work-line]')).toHaveCount(4);
      await expect(status).toContainText(zh ? "已结束 3 个微任务" : "Ended 3 micro tasks");
      await expect(status).toContainText(zh ? "1 个微任务在结束前已交付，没有结束" : "1 micro tasks were delivered before they could be ended");
      await expect(status).toContainText(zh ? "1 个微任务的停止未确认，没有写结论，可以重试"
        : "1 micro tasks are not confirmed stopped, so no conclusion was recorded; you can retry");
      await expect(status).toContainText(zh ? "1 个微任务没有结束：记录冲突；请刷新核对后重试。"
        : "1 micro tasks were not ended: The records conflict; refresh, check them and try again.");
      await expect(status).not.toContainText("Synthetic checkout cleanup is blocked");
      await expect(status).not.toContainText("STORAGE_INCOMPLETE");
      await page.screenshot({ path: join(shots, `end-mixed-${locale}.png`) });
      const retry = page.getByRole("button", { name: zh ? "重试结束" : "Retry end" });
      try {
        await expect(retry).toBeDisabled();
      } finally { releaseRead(); }
      await expect(retry).toBeEnabled();
      await retry.click();
      const again = page.getByRole("dialog", { name: zh ? "结束宏任务" : "End macro task" });
      await expect(again.locator("#end-macro-task-counts")).toContainText(zh ? "结束 2 个微任务" : "End 2 micro tasks");
      await expect(again.locator("#end-macro-task-counts")).toContainText(zh ? "保留 2 个已交付微任务" : "Keep 2 delivered micro tasks");
      await page.screenshot({ path: join(shots, `end-mixed-again-${locale}.png`) });
      expect(commands).toHaveLength(1);
      await again.getByRole("button", { name: zh ? "取消" : "Cancel", exact: true }).click();
      expect(commands).toHaveLength(1);
    });

    test("replays an unknown end with its original identity even after no work remains", async ({ page }) => {
      const commands: Command[] = [];
      let unknown = true;
      let noUnfinished = false;
      await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
        const response = await route.fetch();
        const data = await response.json();
        if (noUnfinished) {
          const row = data.groups.flatMap((group: { macroTasks: { macroTaskId: string }[] }) => group.macroTasks)
            .find((item: { macroTaskId: string }) => item.macroTaskId === A);
          row.counts.unfinished = 0;
        }
        await route.fulfill({ response, json: data });
      });
      await page.route("**/api/command", async route => {
        if (route.request().postDataJSON().operation === "macro_task_end" && unknown) {
          unknown = false;
          const response = await route.fetch(); // The synthetic service now owns the first receipt.
          expect(response.status()).toBe(200);
          noUnfinished = true;
          await route.fulfill({ response, json: { ok: true, result: { macroTaskId: A, results: "malformed", duplicate: false } } });
        } else await route.fallback();
      });
      page.on("request", request => {
        if (request.method() === "POST" && new URL(request.url()).pathname === "/api/command"
          && request.postDataJSON().operation === "macro_task_end") commands.push(request.postDataJSON());
      });
      await open(page, `#tasks/${A}`);
      await more(page).click();
      await page.getByRole("menuitem", { name: zh ? "结束" : "End", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: zh ? "结束宏任务" : "End macro task" });
      await dialog.getByRole("button", { name: zh ? "确认结束" : "End", exact: true }).click();
      const status = page.locator('[data-slot=macro-work-state][data-action=end]');
      await expect(status).toContainText(zh ? "结束的结果未知" : "The end result is unknown");
      await more(page).click();
      await expect(page.getByRole("menuitem", { name: zh ? "重试结束" : "Retry end", exact: true })).toHaveCount(0);
      await page.keyboard.press("Escape");
      const retry = page.getByRole("button", { name: zh ? "结束" : "End", exact: true });
      await expect(retry).toBeVisible();
      const replayResponse = page.waitForResponse(response => response.request().postDataJSON()?.operation === "macro_task_end");
      await retry.click();
      expect((await (await replayResponse).json()).result.duplicate).toBe(true);
      expect(commands).toHaveLength(2);
      expect(commands[1]).toEqual(commands[0]);
      await expect(page.getByRole("dialog")).toHaveCount(0);
    });

    test("marks a confirmed paused turn and states the interruption in its readout and detail", async ({ page }) => {
      const id = "execution:att-b6-1";
      await page.route(new RegExp(`/api/macro-tasks/${B}/timeline(?:\\?|$)`), async route => {
        const response = await route.fetch();
        const data = await response.json();
        const span = data.spans.find((item: { spanId: string }) => item.spanId === id);
        span.terminationReason = "pause";
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

    test("explains a paused queue in the micro task detail", async ({ page }) => {
      await page.route("**/api/tasks/preview-run-b5", async route => {
        const response = await route.fetch();
        const task = await response.json();
        task.state = "queued";
        task.queueReason = "paused";
        await route.fulfill({ response, json: task });
      });
      await open(page, `#tasks/${B}/preview-run-b5`);
      await expect(page.locator("[data-slot=detail-queue-reason]")).toHaveText(zh
        ? "工作已暂停，等待用户在控制台继续。"
        : "Work is paused, waiting for the user to resume it in the console.");
    });
  });
}

for (const locale of ["zh-CN", "en-US"] as const) for (const [width, theme] of [[320, "dark"], [800, "light"]] as const) {
  test.describe(`paused header geometry in ${locale} at ${width}px`, () => {
    test.use({ locale, viewport: { width, height: 900 }, colorScheme: theme, reducedMotion: "reduce" });
    test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: "wait" }); });
    test("keeps counts, pause state and action inside the header", async ({ page }) => {
      await mkdir(shots, { recursive: true });
      await pausedViews(page, { [A]: true });
      await open(page, `#tasks/${A}`);
      const header = page.locator("[data-slot=macro-overview-header]");
      const status = header.locator("[data-slot=macro-pause-state]");
      const action = header.locator("[data-slot=macro-menu-button]");
      await expect(status).toHaveText(locale === "zh-CN" ? /已暂停 · \d{1,2}:\d{2}/ : /Paused · \d{1,2}:\d{2}/);
      await expect(action).toBeVisible();
      const layout = await header.evaluate(element => {
        const frame = element.getBoundingClientRect();
        const slots = ["macro-overview-counts", "macro-pause-state", "macro-menu-button"];
        return slots.map(slot => {
          const node = element.querySelector(`[data-slot="${slot}"]`)!;
          const box = node.getBoundingClientRect();
          return { slot, left: box.left, right: box.right, top: box.top, bottom: box.bottom,
            frameLeft: frame.left, frameRight: frame.right, frameTop: frame.top, frameBottom: frame.bottom };
        });
      });
      for (const box of layout) {
        expect(box.left, `${box.slot} begins inside header`).toBeGreaterThanOrEqual(box.frameLeft - 1);
        expect(box.right, `${box.slot} ends inside header`).toBeLessThanOrEqual(box.frameRight + 1);
        expect(box.top, `${box.slot} begins inside header`).toBeGreaterThanOrEqual(box.frameTop - 1);
        expect(box.bottom, `${box.slot} ends inside header`).toBeLessThanOrEqual(box.frameBottom + 1);
      }
      for (let first = 0; first < layout.length; first++) for (let second = first + 1; second < layout.length; second++) {
        const a = layout[first]!, b = layout[second]!;
        expect(a.right <= b.left + 1 || b.right <= a.left + 1 || a.bottom <= b.top + 1 || b.bottom <= a.top + 1,
          `${a.slot} and ${b.slot} have separate space`).toBe(true);
      }
      await expectNoSidewaysScroll(page);
      await page.screenshot({ path: join(shots, `paused-header-${locale}-${width}-${theme}.png`) });
    });
  });
}
