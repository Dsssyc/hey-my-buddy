import { expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const captures = fileURLToPath(new URL("../../../tmp/readability-r13", import.meta.url));
test.use({ locale: "en-US", timezoneId: "America/New_York", reducedMotion: "reduce" });
test("starts time statistics with both dated local record instants, including last activity while running", async ({ page }) => {
  await open(page, `#tasks/${macro}`);
  for (const { theme, width, language, title, labels } of [
    { theme: "dark", width: 1440, language: "zh", title: "时间统计", labels: ["开始", "最后活动"] },
    { theme: "dark", width: 390, language: "zh", title: "时间统计", labels: ["开始", "最后活动"] },
    { theme: "light", width: 1440, language: "en", title: "Time statistics", labels: ["Started", "Last activity"] },
    { theme: "light", width: 390, language: "en", title: "Time statistics", labels: ["Started", "Last activity"] },
  ]) {
    await page.evaluate(({ theme, language }) => {
      localStorage.setItem("hey-my-buddy.console.theme", theme);
      localStorage.setItem("hey-my-buddy.language", language);
    }, { theme, language });
    await page.setViewportSize({ width, height: 1000 });
    const read = page.waitForResponse(response => new URL(response.url()).pathname === `/api/macro-tasks/${macro}/timeline` && response.ok());
    await page.reload();
    const timeline = await (await read).json() as MacroTaskTimeline;
    expect(timeline.macroTask.counts.active).toBeGreaterThan(0);
    const recorded = [timeline.macroTask.createdAt!, timeline.macroTask.lastActivityAt!];
    const expected = await page.evaluate(({ language, recorded }) => {
      const formatter = new Intl.DateTimeFormat(language === "zh" ? "zh-CN" : "en", { dateStyle: "medium", timeStyle: "medium", hourCycle: "h23" });
      return recorded.map(value => formatter.format(new Date(value)));
    }, { language, recorded });
    await page.getByRole("button", { name: title, exact: true }).click();
    const dialog = page.getByRole("dialog", { name: title, exact: true });
    const first = dialog.locator('[data-slot=time-stats-body] > :first-child');
    await expect(first).toHaveAttribute("data-slot", "time-stats-instants");
    await expect(first.locator("dt")).toHaveText(labels);
    await expect(first.locator("time")).toHaveText(expected);
    for (let index = 0; index < 2; index++) await expect(first.locator("time").nth(index)).toHaveAttribute("datetime", recorded[index]!);
    expect(expected.every(value => value.includes("2026"))).toBe(true);
    if (width === 1440) {
      const [started, last] = await first.locator("time").all();
      expect(Math.abs((await started!.boundingBox())!.y - (await last!.boundingBox())!.y)).toBeLessThanOrEqual(1);
    }
    const card = (await dialog.boundingBox())!;
    expect(card.x).toBeGreaterThanOrEqual(0); expect(card.x + card.width).toBeLessThanOrEqual(width);
    await mkdir(captures, { recursive: true });
    await page.screenshot({ path: `${captures}/stats-${theme}-${width}.png` });
    await page.keyboard.press("Escape");
  }
});
