import { expect } from "@playwright/test";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import type { Snapshot } from "../src/types";
import { open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const run = "preview-run-a1";

for (const theme of ["dark", "light"] as const) for (const labelForm of ["catalog", "nonstandard", "repeated-effort"] as const) {
  test.describe(`model ids with ${labelForm} labels in ${theme}`, () => {
    test.use({ locale: "zh-CN", viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });

    test("uses model ids in the timeline and micro task detail with one effort in each name", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await page.route("**/api/console", async route => {
        const response = await route.fetch();
        const snapshot: Snapshot = await response.json();
        snapshot.buddies = snapshot.buddies.map(buddy => ({
          ...buddy,
          label: labelForm === "catalog" ? `Directory display ${buddy.model.toUpperCase()} · ${buddy.effort}`
            : labelForm === "nonstandard" ? `Directory display ${buddy.model.toUpperCase()} · ${buddy.effort} (catalog)`
              : `Directory display ${buddy.model.toUpperCase()} · ${buddy.effort} · ${buddy.effort}`,
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
      await expect(name.locator(":scope > span:last-child")).toHaveText("gpt-5-codex · high");
      // The old hard-coded map renamed this model even without a directory row.
      await expect(page.locator('[data-slot=timeline-row-name]:visible').filter({ hasText: "claude-opus-5-5 · high" }).first()).toBeVisible();
      await expect(page.locator('[data-slot=task-work]')).not.toContainText("Directory display");
      await name.click();
      const detail = page.locator('[data-slot=task-detail]');
      await expect(detail).toBeVisible();
      const turn = detail.locator('[data-slot=detail-strip] [role=button][aria-label*="gpt-5-codex · high"]').first();
      await expect(turn).toBeAttached();
      // Each readout contains the short model/effort name and the complete name;
      // check each separately so their intentional coexistence is preserved.
      expect(await turn.getAttribute("aria-label")).toContain("gpt-5-codex · high · codex / openai / gpt-5-codex / high");
      await turn.click();
      await expect(detail.locator('[data-slot=detail-band]').first()).toContainText("codex / openai / gpt-5-codex / high");
      await expect(detail).not.toContainText("Directory display");
      await expect(name.locator(":scope > span:last-child")).toHaveText("gpt-5-codex · high");
    });
  });
}
