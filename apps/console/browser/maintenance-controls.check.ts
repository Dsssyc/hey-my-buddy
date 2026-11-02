import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { open, test, expectNoSidewaysScroll, expectTextUncut } from "./preview";

const captures = fileURLToPath(new URL("../../../tmp/maintenance-no-budget/", import.meta.url));
const taskId = "preview-run-maintenance-1";
const finished = "2026-10-29T06:05:01Z";

for (const theme of ["dark", "light"]) for (const width of [1440, 390]) for (const state of ["running", "failed"]) {
  test.describe(`maintenance controls ${state} ${theme} ${width}px`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 1200 }, reducedMotion: "reduce" });
    test("shows activity or retry beside the existing execution action", async ({ page }) => {
      const commands: { operation: string; params: object }[] = [];
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await page.route("**/api/console", async route => {
        const response = await route.fetch();
        const snapshot = await response.json();
        snapshot.maintenance.runs = [{ ...snapshot.maintenance.runs[0], state,
          finishedAt: state === "failed" ? finished : null,
          reason: state === "failed" ? { code: "router-budget-exhausted", params: {} } : null,
          usage: { attempts: [{ attemptId: "maintenance-attempt", elapsedMs: state === "failed" ? 301000 : null, toolCalls: null,
            tokenUsage: state === "failed" ? { inputTokens: 120, cachedInputTokens: null, outputTokens: 7, source: "synthetic-preview", scope: "attempt" } : null }] },
        }];
        await route.fulfill({ response, json: snapshot });
      });
      await page.route(`**/api/tasks/${taskId}`, route => route.fulfill({ json: { runId: taskId,
        activity: { phase: "streaming-model", observedAt: new Date(Date.now() - 120000).toISOString(),
          lastNativeActivityAt: new Date(Date.now() - 120000).toISOString(), eventSeq: 12 },
      } }));
      await page.route("**/api/command", async route => {
        const command = route.request().postDataJSON();
        if (!["task_cancel", "maintenance_start"].includes(command.operation)) return route.continue();
        commands.push(command);
        await route.fulfill({ json: { ok: true, result: command.operation === "maintenance_start"
          ? { status: "running", runId: "maintenance-next" } : { alreadyTerminal: false } } });
      });
      await open(page, "#buddies/roles");
      const row = page.locator('[data-slot="maintenance-record"]');
      await row.scrollIntoViewIfNeeded();
      if (state === "running") {
        const live = row.locator('[data-slot="detail-live"]');
        await expect(live).toContainText("模型输出中");
        await expect(live).toContainText("上报");
        await expectTextUncut(live.locator("span"));
        const phase = (await live.getByText("模型输出中").boundingBox())!;
        const reported = (await live.getByText(/^上报/).boundingBox())!;
        expect(reported.x).toBeGreaterThanOrEqual(phase.x + phase.width);
        expect(reported.x - phase.x - phase.width).toBeLessThanOrEqual(phase.height);
        expect(Math.abs(reported.y - phase.y)).toBeLessThan(phase.height);
        await live.getByText("模型输出中").hover();
        await page.waitForTimeout(700);
        await expect(page.getByRole("tooltip")).toHaveCount(0);
        const stop = row.getByRole("button", { name: "停止", exact: true });
        await stop.focus();
        await expect(stop).toBeFocused();
      } else {
        await expect(row).toContainText("Router 超出了这次调用的时限或工具调用上限。");
        const retry = row.locator('[data-slot="maintenance-retry"]');
        const next = await page.evaluate(value => new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "medium", hourCycle: "h23" }).format(Date.parse(value) + 3600000), finished);
        await expect(retry).toContainText(`下次自动运行 ${next}`);
        await expect(retry.getByRole("button", { name: "立即运行" })).toBeVisible();
        await expectTextUncut(retry.locator("span"));
      }
      await expectNoSidewaysScroll(page);
      if (width === 1440) {
        await mkdir(captures, { recursive: true });
        await page.screenshot({ path: join(captures, `maintenance-${state}-${theme}-1440.png`), fullPage: true });
      }
      await row.getByRole("button", { name: state === "running" ? "停止" : "立即运行", exact: true }).click();
      await expect.poll(() => commands).toEqual([{ operation: state === "running" ? "task_cancel" : "maintenance_start",
        params: state === "running" ? { runId: taskId } : {} }]);
      if (state === "running") await expect(row.getByRole("button", { name: "正在停止" })).toBeDisabled();
    });
  });
}

for (const theme of ["dark", "light"]) for (const width of [1440, 390]) {
  test.describe(`maintenance history ${theme} ${width}px`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 1200 }, reducedMotion: "reduce" });
    test("shows no future run or retry on a failure followed by a completed batch", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await page.route("**/api/console", async route => {
        const response = await route.fetch();
        const snapshot = await response.json();
        const latest = snapshot.maintenance.runs[0];
        snapshot.maintenance.runs = [latest, { ...latest, maintenanceRunId: "maintenance-old-failure",
          taskId: "maintenance-old-task", state: "failed",
          reason: { code: "router-budget-exhausted", params: {} },
          startedAt: "2026-09-26T06:00:00Z", finishedAt: "2026-09-26T06:05:01Z",
        }];
        await route.fulfill({ response, json: snapshot });
      });
      await open(page, "#buddies/roles");
      const records = page.locator('[data-slot="maintenance-record"]');
      await expect(records).toHaveCount(2);
      const older = records.nth(1);
      await older.scrollIntoViewIfNeeded();
      await expect(older).toContainText("Router 超出了这次调用的时限或工具调用上限。");
      await expect(older.getByRole("definition")).toHaveCount(3);
      await expect(older.locator('[data-slot="maintenance-retry"]')).toHaveCount(0);
      await expect(older.getByRole("button", { name: "立即运行" })).toHaveCount(0);
      await expectNoSidewaysScroll(page);
    });
  });
}
