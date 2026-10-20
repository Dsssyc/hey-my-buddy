import { expect, type Page } from "@playwright/test";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { expectNoSidewaysScroll, expectTextUncut, open, screenshots as shots, settle, test } from "./preview";

const MACRO_A = "macro-11111111-1111-4111-8111-111111111111";
const MACRO_B = "macro-22222222-2222-4222-8222-222222222222";
const row = (page: Page, id: string) => page.locator(`[data-slot=timeline-row-name][data-run-id="${id}"]:visible`);

async function timeline(page: Page, macro: string) {
  await page.clock.setFixedTime(new Date("2026-09-27T10:00:00.000Z"));
  const read = page.waitForResponse(response => new URL(response.url()).pathname === `/api/macro-tasks/${macro}/timeline`);
  await open(page, `#tasks/${macro}`);
  const response = await read;
  expect(response.headers()["x-hey-my-buddy-preview"]).toBe("synthetic-fixture-data");
  await expect(page.locator("[data-slot=timeline-axis]")).toBeVisible();
  await expect(page.locator(`[data-slot=macro-list] [data-macro-task-id="${macro}"]`)).toHaveCount(1);
  return await response.json() as MacroTaskTimeline;
}

for (const locale of ["zh-CN", "en-US"] as const) for (const theme of ["light", "dark"] as const) {
  test.describe(`timeline states and focus ${locale} ${theme}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    });

    test("keeps correction hints closed while detail moves focus from the timeline", async ({ page }) => {
      await timeline(page, MACRO_A);
      const correction = row(page, "preview-run-a2");
      const segment = page.locator('[data-key="span:execution:att-a2-2"]');
      await segment.click();
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

    test("uses buddy fills for executions and keeps routing hollow", async ({ page }) => {
      await timeline(page, MACRO_A);
      for (const outcome of ["running", "failed", "unknown"]) {
        const bar = page.locator(`[data-slot=execution-bar][data-outcome=${outcome}]`).first();
        await expect(bar).toBeVisible();
        expect(await bar.getAttribute("style")).toMatch(/fill:/);
      }
      await expect(page.locator('[data-slot=execution-breath]')).not.toHaveCount(0);
      await expect(page.locator('[data-slot=execution-failed]')).not.toHaveCount(0);
      for (const bar of await page.locator('[data-slot=routing-bar]').all()) {
        const fill = await bar.evaluate(node => getComputedStyle(node).fill);
        const card = await page.locator('[data-slot=task-work]').evaluate(node => getComputedStyle(node).getPropertyValue("--surface").trim());
        expect(fill).toBe(await page.evaluate(value => { const element = document.createElement("span"); element.style.color = value; document.body.append(element); const color = getComputedStyle(element).color; element.remove(); return color; }, card));
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
      const live = await page.locator('[data-slot=task-work]').evaluate(node => getComputedStyle(node).color);
      await expect(page.locator("[data-slot=macro-stop-state]")).toHaveCSS("color", live);
      for (const id of ["preview-run-b7", "preview-run-b8"]) {
        await expect(row(page, id).getByText(stopping, { exact: true })).toHaveCSS("color", live);
        await expect(row(page, id).locator("[data-slot=state-mark]")).toHaveAttribute("data-kind", "live");
        await expect(row(page, id).locator("[data-slot=state-mark] circle")).toHaveCSS("fill", live);
      }
      for (const [id, lead] of [["preview-run-b6", "preview-run-b3"], ["preview-run-b8", "preview-run-b7"]]) {
        const stops = data.events.filter(event => event.runId === id && event.kind === "cancel");
        expect(stops).toHaveLength(1);
        const event = stops[0]!;
        expect(event.actor).toEqual({ role: "lead", id: lead });
        expect(event.eventKind).toBe("workflow.cancelled");
        const marker = page.locator(`[data-key="event:${event.seq}"]`);
        await expect(marker).toHaveAccessibleName(new RegExp(stop));
        await marker.focus();
        await expect(page.locator("[data-slot=timeline-status]")).toContainText(stop);
        await settle(page);
        if (locale === "zh-CN") await page.screenshot({ path: `${shots}/lead-${id === "preview-run-b6" ? "stopped" : "stop-pending"}-timeline-${theme}.png` });
        await marker.press("Enter");
        const detail = page.locator("[data-slot=task-detail]");
        await expect(detail).toBeVisible();
        const step = page.locator("[data-slot=detail-strip] [aria-pressed=true]");
        await expect(step).toHaveAccessibleName(new RegExp(stop));
        const pane = detail.locator("[data-slot=detail-pane]");
        await expect(pane.getByRole("heading", { level: 3, name: stop, exact: true })).toBeVisible();
        await expect(pane).toContainText(stop);
        await expect(pane).toContainText(lead!);
        await expectTextUncut(pane.locator("h3, dt, dd, p"));
        await expect(page.locator("[data-slot=detail-status]")).toHaveText(id === "preview-run-b8" ? stopping : zh ? "已结束" : "Ended");
        if (id === "preview-run-b8") await expect(page.locator("[data-slot=detail-status]")).toHaveCSS("color", live);
        await expectNoSidewaysScroll(page);
        await settle(page);
        if (locale === "zh-CN") await page.screenshot({ path: `${shots}/lead-${id === "preview-run-b6" ? "stopped" : "stop-pending"}-${theme}.png` });
        await page.keyboard.press("Escape");
        await expect(detail).toBeHidden();
      }
      await expect(row(page, "preview-run-b7")).toHaveAccessibleName(new RegExp(stopping));
      await row(page, "preview-run-b7").press("Enter");
      await expect(page.locator("[data-slot=detail-status]")).toHaveText(stopping);
      await expect(page.locator("[data-slot=detail-status]")).toHaveCSS("color", live);
      await expect(page.locator("[data-slot=detail-status]")).toHaveAttribute("aria-description",
        zh ? "停止未确认 · 待核实执行 1 项" : "Stop unconfirmed · executions to verify: 1");
      await expect(page.locator("[data-slot=macro-stop-state]")).toHaveText(stopping);
      const hostStop = zh ? "Host 停止" : "Stopped by Host";
      await page.locator("[data-slot=detail-strip]").getByRole("button", { name: new RegExp(hostStop) }).click();
      await expect(page.locator("[data-slot=detail-pane]").getByRole("heading", { level: 3, name: hostStop, exact: true })).toBeVisible();
      await settle(page);
      if (locale === "zh-CN") await page.screenshot({ path: `${shots}/lead-stopping-${theme}.png` });
    });
  });
}
