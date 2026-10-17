import { expect, type Page } from "@playwright/test";
import { open, test, expectNoSidewaysScroll, expectWholeAndApart } from "./preview";
import type { MacroTaskTimeline } from "../src/macro-task-types";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const timelinePath = `/api/macro-tasks/${macro}/timeline`;

function row(page: Page, runId: string) {
  return page.locator(`[data-slot=timeline-row-name][data-key="row:${runId}"]`);
}

for (const { locale, language, fixes } of [
  { locale: "zh-CN", language: "zh-CN", fixes: "修正" },
  { locale: "en-US", language: "en", fixes: "Fixes" },
]) for (const width of [1440, 1024]) {
  test.describe(`corrections in ${locale} at ${width}px`, () => {
    test.use({ locale, viewport: { width, height: 1000 } });
    test("renders linked corrections", async ({ page }) => {
    const response = page.waitForResponse(reply => new URL(reply.url()).pathname === timelinePath);
    await open(page, "#tasks");
    await page.locator(`[data-macro-task-id="${macro}"] button`).click();
    const data = await response;
    expect(data.headers()["x-hey-my-buddy-preview"]).toBe("synthetic-fixture-data");
    const timeline = await data.json() as MacroTaskTimeline;
    expect(timeline.rows.find(item => item.runId === "preview-run-a2")).toMatchObject({
      fixes: "preview-run-a1",
    });
    expect(timeline.rows.find(item => item.runId === "preview-run-a3")?.fixes).toBe("preview-run-a1");
    expect(timeline.rows.find(item => item.runId === "preview-run-a1")?.fixedBy).toEqual(["preview-run-a2", "preview-run-a3"]);
    expect(timeline.rows.find(item => item.runId === "preview-run-a1-h1")?.fixes).toBeUndefined();

    const first = row(page, "preview-run-a1");
    const second = row(page, "preview-run-a2");
    const third = row(page, "preview-run-a3");
    await expect(first.locator("[data-slot=fixed-by]")).toHaveText("↩ 2");
    await expect(second.locator("[data-slot=fixed-by]")).toHaveCount(0);
    await expect(second.locator("[data-slot=fix-link]")).toHaveText("↪");
    await expect(third.locator("[data-slot=fix-link]")).toHaveText("↪");
    await expect(second).toHaveAttribute("data-fixes", "preview-run-a1");
    await expect(third).toHaveAttribute("data-fixes", "preview-run-a1");
    await expect(second.locator("[data-slot=fix-link]")).toHaveAttribute("aria-label", /.+/);
    await expect(page.locator("html")).toHaveAttribute("lang", language);
    for (const label of [first, second, third]) {
      await expectWholeAndApart(label, label.locator(":scope > span"));
    }
    await expectNoSidewaysScroll(page);
    });
  });
}
