import { expect } from "@playwright/test";
import { open, test } from "./preview";
import type { MacroTaskTimeline } from "../src/macro-task-types";

const group = "run:preview-run-c1";

for (const { locale, labels } of [
  { locale: "zh-CN", labels: { command: "命令执行", external: "外部执行" } },
  { locale: "en-US", labels: { command: "Command execution", external: "External execution" } },
]) {
  test.describe(`infrastructure executions in ${locale}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 1000 } });

    test("shows the recorded adapter throughout the timeline and detail with no model buddy", async ({ page }) => {
      const response = page.waitForResponse(reply => decodeURIComponent(new URL(reply.url()).pathname).endsWith(`/macro-tasks/${group}/timeline`));
      await open(page, "#tasks");
      await page.getByRole("button", { name: /历史独立微任务|Historical standalone micro tasks/ }).click();
      await page.locator(`[data-macro-task-id="${group}"] button`).click();
      const timeline = await (await response).json() as MacroTaskTimeline;
      for (const adapter of ["command", "external"] as const) {
        const runId = `preview-run-c1-${adapter}`;
        const row = timeline.rows.find(item => item.runId === runId);
        const span = timeline.spans.find(item => item.runId === runId && item.kind === "execution");
        expect(row).toMatchObject({ adapter, buddy: null });
        expect(span).toMatchObject({ adapter, buddy: null });

        const timelineRow = page.locator(`.tl-label[data-key="row:${runId}"]`);
        await expect(timelineRow).toContainText(labels[adapter]);
        const execution = page.locator(`.sp[data-key="span:execution:att-c1-${adapter}"]`);
        await expect(execution).toHaveAccessibleName(new RegExp(labels[adapter]));
        await execution.click();
        await page.getByRole("button", { name: "展开检查器", exact: true }).click();
        await expect(page.locator(".inspector-card")).toContainText(labels[adapter]);
        await page.getByRole("button", { name: "收起检查器", exact: true }).click();

        await timelineRow.dblclick();
        const detail = page.locator(".detail-panel");
        await expect(detail.locator(".assignment-line")).toContainText(labels[adapter]);
        await expect(detail.locator(".routing-link")).toHaveCount(0);
        await detail.locator(".record-facts > summary").click();
        await expect(detail.locator(".record-facts")).toContainText(labels[adapter]);
        await detail.getByRole("button", { name: "关闭详情" }).click();
      }
    });
  });
}
