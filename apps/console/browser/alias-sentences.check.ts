import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect } from "@playwright/test";
import type { Snapshot } from "../src/types";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const identity = { harness: "codex", provider: "openai", model: "gpt-5-codex", effort: "high" };
const name = "codex / openai / gpt-5-codex / high";
for (const locale of ["zh-CN", "en-US"]) for (const theme of ["dark", "light"]) test.describe(`alias sentences ${locale} ${theme}`, () => {
  test.use({ locale, viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  test("places tags inside a routing reason and preferred-buddy tooltip and keeps readout names stable", async ({ page }) => {
    const zh = locale === "zh-CN";
    let alias: string | null = "Review desk";
    let buddyId = "";
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await page.route("**/api/console", async route => {
      const response = await route.fetch();
      const snapshot: Snapshot = await response.json();
      const buddy = snapshot.buddies.find(buddy => buddy.model === identity.model && buddy.effort === identity.effort)!;
      buddyId = buddy.buddyId;
      buddy.alias = alias;
      await route.fulfill({ response, json: snapshot });
    });
    await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
      const response = await route.fetch();
      const timeline: MacroTaskTimeline = await response.json();
      for (const span of timeline.spans) if (span.buddy?.model === identity.model) span.buddy.effort = identity.effort;
      for (const span of timeline.spans) if (span.kind === "routing" && span.routing) {
        span.routing.reason = { code: "alias.conflict", params: { conflictingBuddy: identity } };
        span.routing.note = null;
      }
      await route.fulfill({ response, json: timeline });
    });
    await page.route("**/api/command", async route => {
      const body = route.request().postDataJSON();
      if (body.operation !== "workflow_get") { await route.fallback(); return; }
      const response = await route.fetch();
      const reply = await response.json();
      for (const entry of reply.result.routingHistory?.entries ?? []) entry.exploration = { quota: true, reselected: true, preferredBuddyId: buddyId };
      await route.fulfill({ response, json: reply });
    });
    await open(page, `#tasks/${macro}/preview-run-a1/2`);
    const pane = page.locator('[data-slot=detail-pane]');
    const reason = pane.locator('[data-slot=fold-body]').first();
    await expect(reason).toContainText(zh ? "这个别名已被" : "This alias is already used by");
    await expect(reason).toContainText(name);
    await expect(reason.locator('[data-slot=alias-tag]')).toHaveText("Review desk");
    await expect(reason).not.toContainText("[");
    await expect(reason).not.toContainText("]");
    await pane.getByText(zh ? "探索" : "Exploration", { exact: true }).hover();
    const tooltip = page.getByRole("tooltip");
    await expect(tooltip).toContainText(zh ? "首选：" : "Preferred: ");
    await expect(tooltip.locator('[data-slot=alias-tag]').first()).toHaveText("Review desk");
    await expect(tooltip).not.toContainText("[");
    await expect(tooltip).not.toContainText("]");
    const captures = fileURLToPath(new URL("../../../tmp/alias-console/round2/captures", import.meta.url));
    await mkdir(captures, { recursive: true });
    await page.screenshot({ path: `${captures}/reason-tooltip-${locale}-${theme}.png` });
    await page.goto(`#tasks/${macro}`);
    const row = page.locator('[data-slot=timeline-row-name][data-run-id=preview-run-a1]:visible');
    await row.hover();
    const readout = page.locator('[data-slot=timeline-readout]');
    await expect(readout).toContainText("gpt-5-codex · high");
    const execution = page.locator(`[data-slot=timeline-row][data-run-id=preview-run-a1] [data-key^="span:"][aria-label*="${name}"]`).first();
    await execution.hover();
    await expect(readout).toContainText(name);
    expect((await readout.innerText()).split(identity.model)).toHaveLength(2);
    await expect(readout.locator('[data-slot=alias-tag]')).toHaveCount(1);
    await expect(readout.locator('[data-slot=alias-tag]')).toHaveText("Review desk");
    await expect(readout).not.toContainText("[");
    await expect(readout).not.toContainText("]");
    const accessible = (await execution.getAttribute("aria-label"))!;
    expect(accessible.split(identity.model)).toHaveLength(2);
    expect(accessible.split("[Review desk]")).toHaveLength(2);
    alias = null;
    await page.reload();
    await execution.hover();
    await expect(readout).toContainText(name);
    expect((await readout.innerText()).split(identity.model)).toHaveLength(2);
    await expect(readout.locator('[data-slot=alias-tag]')).toHaveCount(0);
  });
});
