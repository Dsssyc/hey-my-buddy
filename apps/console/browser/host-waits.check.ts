import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect } from "@playwright/test";
import { expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, settle, test } from "./preview";

const macro = "macro-33333333-3333-4333-8333-333333333333";
const captures = fileURLToPath(new URL("../../../tmp/host-waits/", import.meta.url));

for (const { locale, width, theme } of [
  { locale: "zh-CN", width: 1280, theme: "light" },
  { locale: "en-US", width: 390, theme: "dark" },
] as const) test.describe(`unconcluded work ${locale} ${width}px ${theme}`, () => {
  test.use({ locale, viewport: { width, height: 900 }, colorScheme: theme, reducedMotion: "reduce" });
  test("counts open failures and cancellations as Host waits and distinguishes concluded failure", async ({ page }) => {
    const zh = locale === "zh-CN";
    const name = `${locale}-${width}-${theme}`;
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await mkdir(captures, { recursive: true });
    await open(page, "#tasks");
    const list = page.locator("[data-slot=macro-list]");
    const entry = list.locator(`[data-macro-task-id="${macro}"] [data-slot=macro-entry]`);
    await expect(entry).toBeVisible();
    await expect(entry.locator('[data-slot="state-mark"]')).toHaveAttribute("data-kind", "hand");
    await expect(entry.locator("[data-slot=macro-entry-counts]")).toHaveCount(0);
    await expect(page.getByRole("link", { name: zh ? /^任务 7 / : /^Tasks 7 / })).toBeVisible();
    await settle(page);
    await page.screenshot({ path: `${captures}/${name}-list.png`, animations: "disabled" });

    await entry.click();
    const header = page.locator("[data-slot=macro-overview-header]");
    const host = header.locator("[data-slot=macro-overview-count-host]");
    await expect(host.locator("dd")).toHaveText("2");
    await expect(host.locator("dt")).toHaveText(zh ? "等待 Host" : "Waiting for Host");
    await expect(host.locator('[data-kind="hand"]')).toHaveCount(1);
    await expect(header.locator("[data-slot=macro-overview-count-failed] dd")).toHaveText("1");
    await expect(header.locator("[data-slot=macro-overview-count-ended] dd")).toHaveText("2");
    await settle(page);
    await expectTextUncut(header.locator("[data-slot=macro-overview-counts] dt, [data-slot=macro-overview-counts] dd"));
    await page.screenshot({ path: `${captures}/${name}-macro.png`, animations: "disabled" });

    if (width === 390) await page.getByRole("button", { name: "‹ Macro tasks", exact: true }).click();
    const filter = list.getByRole("tab", { name: new RegExp(`^${zh ? "等待 Host" : "Waiting for Host"} `) });
    await filter.click();
    await expect(filter).toHaveAttribute("aria-selected", "true");
    await expect(list.getByRole("button", { name: zh ? /独立微任务/ : /Standalone micro tasks/ })).toHaveCount(0);
    await expect(entry).toBeVisible();
    await entry.click();

    for (const [run, label, mark] of [
      ["preview-run-d2", zh ? "失败，等待 Host" : "Failed, waiting for Host", "hand"],
      ["preview-run-d3", zh ? "已取消，等待 Host" : "Cancelled, waiting for Host", "hand"],
      ["preview-run-d4", zh ? "失败" : "Failed", "fail"],
    ]) {
      await page.goto(`#tasks/${macro}/${run}`);
      const head = page.locator("[data-slot=detail-header]");
      const status = head.locator("[data-slot=detail-status]");
      await expect(status).toHaveText(label);
      await expect(status.locator("[data-slot=state-mark]")).toHaveAttribute("data-kind", mark);
      await expect(status).toHaveClass(mark === "hand" ? /text-hand/ : /text-fail/);
      await settle(page);
      await expectTextUncut(status);
      await expectWholeAndApart(head, status);
      await expectNoSidewaysScroll(page);
      if (run === "preview-run-d2") await page.screenshot({ path: `${captures}/${name}-detail.png`, animations: "disabled" });
    }

  });
  test("keeps failed and cancelled helpers terminal after their root is accepted", async ({ page }) => {
    const zh = locale === "zh-CN";
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, `#tasks/${macro}`);
    const failed = page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-d5-h1"]:visible');
    const cancelled = page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-d5-h2"]:visible');
    await expect(failed).toHaveAccessibleName(new RegExp(zh ? "执行失败" : "Execution failed"));
    await expect(failed).toHaveAttribute("data-state-tone", "fail");
    await expect(cancelled).toHaveAccessibleName(new RegExp(zh ? "已取消" : "Cancelled"));
    await expect(cancelled).toHaveAttribute("data-state-tone", "past");
    await expect(cancelled.getByText(zh ? "已取消" : "Cancelled", { exact: true })).toBeVisible();
    await expect(cancelled.getByText(zh ? "已取消" : "Cancelled", { exact: true })).not.toHaveClass(/text-hand|text-fail/);
    await expect(page.locator('[data-key="span:execution:att-d5h1-1"] [data-slot=execution-failed]')).toBeVisible();
    if (zh) {
      await mkdir(captures, { recursive: true });
      await expect(page.locator(`[data-macro-task-id="${macro}"] [data-slot=macro-entry]`)).toBeVisible();
      await settle(page);
      await page.screenshot({ path: `${captures}/${locale}-${width}-${theme}-helpers.png`, animations: "disabled" });
    }
    for (const [run, label, mark] of [
      ["preview-run-d5-h1", zh ? "失败" : "Failed", "fail"],
      ["preview-run-d5-h2", zh ? "已结束" : "Ended", "idle"],
    ]) {
      await page.goto(`#tasks/${macro}/${run}`);
      const status = page.locator("[data-slot=detail-status]");
      await expect(status).toHaveText(label);
      await expect(status.locator("[data-slot=state-mark]")).toHaveAttribute("data-kind", mark);
      await expect(status).not.toHaveClass(/text-hand/);
    }
  });
});
