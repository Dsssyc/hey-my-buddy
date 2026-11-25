import { expect } from "@playwright/test";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import type { Snapshot } from "../src/types";
import { expandDetailTimeline, open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const run = "preview-run-a1";

for (const theme of ["dark", "light"] as const) for (const aliased of [false, true]) {
  test.describe(`model ids with alias=${aliased} in ${theme}`, () => {
    test.use({ locale: "zh-CN", viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });

    test("uses model ids in the timeline and micro task detail with one effort in each name", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await page.route("**/api/console", async route => {
        const response = await route.fetch();
        const snapshot: Snapshot = await response.json();
        snapshot.buddies = snapshot.buddies.map(buddy => ({
          ...buddy,
          alias: aliased && buddy.model === "gpt-5-codex" && buddy.effort === "high" ? "Review desk" : null,
        }));
        await route.fulfill({ response, json: snapshot });
      });
      await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
        const response = await route.fetch();
        const timeline: MacroTaskTimeline = await response.json();
        // Exercise a recorded effort on a historical span as well as the row;
        // the preview's default historical spans leave that field unrecorded.
        timeline.spans = timeline.spans.map(span => span.buddy?.model === "gpt-5-codex"
          ? { ...span, buddy: { ...span.buddy, effort: "high" } } : span);
        await route.fulfill({ response, json: timeline });
      });
      await open(page, `#tasks/${macro}`);
      const name = page.locator(`[data-slot=timeline-row-name][data-run-id="${run}"]:visible`);
      await expect(name.locator(":scope > span:last-child")).toContainText("gpt-5-codex · high");
      await expect(name.locator('[data-slot=alias-tag]')).toHaveCount(aliased ? 1 : 0);
      if (aliased) await expect(name.locator('[data-slot=alias-tag]')).toHaveText("Review desk");
      // The old hard-coded map renamed this model even without a directory row.
      await expect(page.locator('[data-slot=timeline-row-name]:visible').filter({ hasText: "claude-opus-5-5 · high" }).first()).toBeVisible();
      await expect(page.locator('[data-slot=task-work]')).not.toContainText("Directory display");
      await name.click();
      const detail = page.locator('[data-slot=task-detail]');
      await expect(detail).toBeVisible();
      await expandDetailTimeline(page);
      const turn = detail.locator(`[data-slot=detail-strip] [role=button][aria-label*="codex / openai / gpt-5-codex / high"]`).first();
      await expect(turn).toBeAttached();
      const readout = (await turn.getAttribute("aria-label"))!;
      expect(readout).toContain("codex / openai / gpt-5-codex / high");
      expect(readout.split("gpt-5-codex")).toHaveLength(2);
      expect(readout.split("[Review desk]")).toHaveLength(aliased ? 2 : 1);
      await turn.click();
      await expect(detail.locator('[data-slot=detail-band]').first()).toContainText("codex / openai / gpt-5-codex / high");
      await expect(detail.locator('[data-slot=detail-band] [data-slot=alias-tag]')).toHaveCount(aliased ? 1 : 0);
      await expect(name.locator(":scope > span:last-child")).toContainText("gpt-5-codex · high");
      await expect(name.locator('[data-slot=alias-tag]')).toHaveCount(aliased ? 1 : 0);
      if (aliased) await expect(name.locator('[data-slot=alias-tag]')).toHaveText("Review desk");
    });
  });
}
