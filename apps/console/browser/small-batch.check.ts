import { expect, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { expectNoSidewaysScroll, expectTextUncut, open, settle, test } from "./preview";

const MACRO_A = "macro-11111111-1111-4111-8111-111111111111";
const MACRO_B = "macro-22222222-2222-4222-8222-222222222222";
const shots = fileURLToPath(new URL("../../../tmp/step4-small-shots", import.meta.url));
const row = (page: Page, id: string) => page.locator(`[data-slot=timeline-row-name][data-run-id="${id}"]:visible`);

async function timeline(page: Page, macro: string) {
  await page.clock.setFixedTime(new Date("2026-09-27T10:00:00.000Z"));
  const read = page.waitForResponse(response => new URL(response.url()).pathname === `/api/macro-tasks/${macro}/timeline`);
  await open(page, `#tasks/${macro}`);
  const response = await read;
  expect(response.headers()["x-hey-my-buddy-preview"]).toBe("synthetic-fixture-data");
  await expect(page.locator("[data-slot=timeline-axis]")).toBeVisible();
  return await response.json() as MacroTaskTimeline;
}

for (const locale of ["zh-CN", "en-US"] as const) for (const theme of ["light", "dark"] as const) {
  test.describe(`small console regressions ${locale} ${theme}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    });

    test("keeps correction hints closed while detail moves focus from the timeline", async ({ page }) => {
      await timeline(page, MACRO_A);
      const correction = row(page, "preview-run-a2");
      const segment = page.locator('[data-key="span:execution:att-a2-2"]');
      await segment.dblclick();
      await expect(page.locator("[data-slot=task-detail]")).toBeVisible();
      await expect(correction).toBeFocused();
      await settle(page);
      await expect(page.getByRole("tooltip")).toBeHidden();
      if (locale === "zh-CN") await page.screenshot({ path: `${shots}/correction-detail-${theme}.png` });
      await page.keyboard.press("Escape");
      await expect(page.locator("[data-slot=task-detail]")).toBeHidden();
      await expect(correction).toBeFocused();
      await expect(page.getByRole("tooltip")).toBeHidden();
      // Keyboard activation also causes a scripted relocation to the row name.
      await segment.focus();
      await segment.press("Enter");
      await expect(page.locator("[data-slot=task-detail]")).toBeVisible();
      await expect(correction).toBeFocused();
      await expect(page.getByRole("tooltip")).toBeHidden();
    });

    test("uses actual state fills for live, failed, unconfirmed and routing bars", async ({ page }) => {
      await timeline(page, MACRO_A);
      for (const [selector, token] of [
        ['[data-slot=execution-bar][data-outcome=running]', "--live"],
        ['[data-slot=execution-bar][data-outcome=failed]', "--fail"],
        ['[data-slot=execution-bar][data-outcome=unknown]', "--hand-wash"],
        ['[data-slot=routing-bar]', "--card"],
      ]) {
        const bars = page.locator(selector!);
        expect(await bars.count(), selector).toBeGreaterThan(0);
        const colors = await bars.evaluateAll((nodes, variable) => nodes.map(node => {
          const style = getComputedStyle(node);
          const context = document.createElement("canvas").getContext("2d")!;
          const color = (value: string) => { context.fillStyle = value; return context.fillStyle; };
          return { actual: color(style.fill), expected: color(style.getPropertyValue(variable)),
            models: Array.from({ length: 6 }, (_, index) => color(style.getPropertyValue(`--model-${index + 1}`))) };
        }), token!);
        for (const color of colors) {
          expect(color.actual, selector).toBe(color.expected);
          expect(color.models, selector).not.toContain(color.actual);
        }
      }
    });

    test("shows Lead stop actors and retains a stopping Lead until owned shutdown is confirmed", async ({ page }) => {
      const data = await timeline(page, MACRO_B);
      const zh = locale === "zh-CN";
      const stop = zh ? "Lead 停止" : "Stopped by Lead";
      const stopping = zh ? "正在停止" : "Stopping";
      expect(data.macroTask.state).toBe("active");
      await expect(page.locator("[data-slot=macro-stop-state]")).toHaveText(stopping);
      await expect(page.locator("[data-slot=macro-overview-counts]")).toContainText(zh ? "进行中" : "Running");
      for (const [id, lead] of [["preview-run-b6", "preview-run-b3"], ["preview-run-b8", "preview-run-b7"]]) {
        const event = data.events.find(event => event.runId === id && event.kind === "cancel" && event.actor?.role === "lead")!;
        expect(event.actor).toEqual({ role: "lead", id: lead });
        // The Lead request and the later Host cancellation remain two facts in
        // the cluster; the first initiator is never borrowed from the later one.
        if (id === "preview-run-b8") {
          const cluster = page.locator(`[data-key="cluster:event:${event.seq}"]`);
          await cluster.focus();
          await expect(page.locator("[data-slot=timeline-status]")).toContainText(stop);
          await settle(page);
          if (locale === "zh-CN") await page.screenshot({ path: `${shots}/lead-stop-pending-timeline-${theme}.png` });
          await cluster.press("Enter");
        }
        const marker = id === "preview-run-b8"
          ? page.locator("[data-slot=popover-content]").getByRole("button", { name: new RegExp(`^${stop}`) })
          : page.locator(`[data-key="event:${event.seq}"]`);
        await expect(marker).toHaveAccessibleName(new RegExp(stop));
        await marker.focus();
        await expect(page.locator("[data-slot=timeline-status]")).toContainText(stop);
        if (locale === "zh-CN" && id === "preview-run-b6") {
          await settle(page);
          await page.screenshot({ path: `${shots}/lead-stopped-timeline-${theme}.png` });
        }
        await marker.press("Enter");
        const detail = page.locator("[data-slot=task-detail]");
        await expect(detail).toBeVisible();
        await expect(page.locator("[data-slot=detail-rail] [aria-current=step]")).toContainText(`Lead · ${lead}`);
        const pane = detail.locator("[data-slot=detail-pane]");
        await expect(pane).toContainText(stop);
        await expect(pane).toContainText(lead!);
        await expectTextUncut(pane.locator("h3, dt, dd, p"));
        await expect(page.locator("[data-slot=detail-status]")).toHaveText(id === "preview-run-b8" ? stopping : zh ? "已结束" : "Ended");
        await expectNoSidewaysScroll(page);
        await settle(page);
        if (locale === "zh-CN") await page.screenshot({ path: `${shots}/lead-${id === "preview-run-b6" ? "stopped" : "stop-pending"}-${theme}.png` });
        await page.keyboard.press("Escape");
        await expect(detail).toBeHidden();
      }
      await expect(row(page, "preview-run-b7")).toHaveAccessibleName(new RegExp(stopping));
      await row(page, "preview-run-b7").press("Enter");
      await expect(page.locator("[data-slot=detail-status]")).toHaveText(stopping);
      await expect(page.locator("[data-slot=detail-status]")).toHaveAttribute("aria-description",
        zh ? "停止未确认，1 项执行尚未核实" : "Stop not confirmed; executions not yet verified: 1");
      await expect(page.locator("[data-slot=macro-stop-state]")).toHaveText(stopping);
      await settle(page);
      if (locale === "zh-CN") await page.screenshot({ path: `${shots}/lead-stopping-${theme}.png` });
    });
  });
}
