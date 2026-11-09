import { expect } from "@playwright/test";
import { open, test } from "./preview";

const taskId = "preview-run-maintenance-1";
const finished = "2026-10-29T06:05:01Z";

for (const theme of ["dark", "light"] as const) for (const width of [1440, 390]) {
  test.describe(`maintenance record controls ${theme} ${width}px`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 1200 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme));

    test("shows native activity and sends stop for a running record", async ({ page }) => {
      const commands: { operation: string; params: object }[] = [];
      await page.route("**/api/console", async route => {
        const response = await route.fetch(); const snapshot = await response.json();
        snapshot.maintenance.runs = [{ ...snapshot.maintenance.runs[0], state: "running", finishedAt: null }];
        await route.fulfill({ response, json: snapshot });
      });
      await page.route(`**/api/tasks/${taskId}`, route => route.fulfill({ json: { runId: taskId,
        activity: { phase: "streaming-model", observedAt: new Date(Date.now() - 120000).toISOString(),
          lastNativeActivityAt: new Date(Date.now() - 120000).toISOString(), eventSeq: 12 },
      } }));
      await page.route("**/api/command", async route => {
        const command = route.request().postDataJSON();
        if (command.operation !== "task_cancel") return route.fallback();
        commands.push(command);
        await route.fulfill({ json: { ok: true, result: { alreadyTerminal: false } } });
      });
      await open(page, "#buddies/router/maintenance");
      const row = page.locator('[data-slot="maintenance-record"]').first();
      await expect(row.locator('[data-slot="detail-live"]')).toContainText("模型输出中");
      const stop = row.getByRole("button", { name: "停止", exact: true });
      await stop.focus();
      await expect(stop).toBeFocused();
      await stop.click();
      await expect.poll(() => commands).toEqual([{ operation: "task_cancel", params: { runId: taskId } }]);
      await expect(row.getByRole("button", { name: "正在停止" })).toBeDisabled();
    });

    test("shows failure text and computes the next run from the shared interval", async ({ page }) => {
      await page.route("**/api/console", async route => {
        const response = await route.fetch(); const snapshot = await response.json();
        snapshot.maintenance.runs = [{ ...snapshot.maintenance.runs[0], state: "failed", finishedAt: finished,
          reason: { code: "router-not-responding", params: { seconds: 60 } } }];
        await route.fulfill({ response, json: snapshot });
      });
      await open(page, "#buddies/router/maintenance");
      const row = page.locator('[data-slot="maintenance-record"]');
      await expect(row).toContainText("Router 的模型在 60 秒内没有开始作答。");
      await expect(row.getByRole("button", { name: "立即运行" })).toHaveCount(0);
      await expect(row.getByRole("button", { name: "停止" })).toHaveCount(0);
      const next = await page.evaluate(value => {
        const date = new Date(Date.parse(value) + 3600000);
        const pad = (part: number) => String(part).padStart(2, "0");
        return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
      }, finished);
      await expect(page.locator('[data-slot="maintenance-next"]')).toContainText(next);
      await expect(page.getByRole("spinbutton", { name: "维护间隔" })).toHaveValue("3600");
    });

    test("leaves an older failed record without an action after a later completed run", async ({ page }) => {
      await page.route("**/api/console", async route => {
        const response = await route.fetch(); const snapshot = await response.json();
        const latest = snapshot.maintenance.runs[0];
        snapshot.maintenance.runs = [latest, { ...latest, maintenanceRunId: "maintenance-old-failure", taskId: "maintenance-old-task",
          state: "failed", reason: { code: "router-not-responding", params: { seconds: 60 } },
          startedAt: "2026-09-26T06:00:00Z", finishedAt: "2026-09-26T06:05:01Z" }];
        await route.fulfill({ response, json: snapshot });
      });
      await open(page, "#buddies/router/maintenance");
      const records = page.locator('[data-slot="maintenance-record"]');
      await expect(records).toHaveCount(2);
      await expect(records.nth(1)).toContainText("Router 的模型在 60 秒内没有开始作答。");
      const older = records.nth(1);
      await expect(older.getByRole("button", { name: /停止|重试|立即运行|立即维护/ })).toHaveCount(0);
      const usage = older.locator('[data-slot="quant-bar-trigger"]');
      await expect(usage).toHaveAttribute("aria-expanded", "false");
      await usage.click();
      await expect(usage).toHaveAttribute("aria-expanded", "true");
      await expect(older.locator('[data-slot="quant-bar-detail"]')).toContainText("缓存输入");
    });
  });
}
