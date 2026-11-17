import { expect, type Locator, type Page } from "@playwright/test";
import { open, settle, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const detail = (page: Page) => page.locator('[data-slot=task-detail]');
const content = (page: Page) => detail(page).locator(':scope > div').last();
const row = (page: Page, runId: string) => page.locator(`[data-slot=timeline-row-name][data-run-id="${runId}"]:visible`);

async function expectOpacityTransition(target: Locator, reduced = false) {
  await expect(target).toHaveCSS("transition-property", reduced ? "none" : "opacity");
  if (!reduced) await expect(target).toHaveCSS("transition-duration", "0.15s");
}

async function listControls(page: Page, reduced = false) {
  const entry = page.locator(`[data-slot=macro-list] [data-macro-task-id="${macro}"]`);
  const group = entry.locator("xpath=ancestor::*[@data-slot='macro-group']");
  const count = group.locator('[data-slot=macro-group-count]');
  const sort = group.locator('[data-slot=macro-sort-button]').locator("..");
  const actions = entry.locator('[data-slot=macro-menu-button]').locator("..");
  for (const target of [count, sort, actions]) await expectOpacityTransition(target, reduced);
  await page.mouse.move(1400, 900);
  await expect(count).toHaveCSS("opacity", "1");
  await expect(actions).toHaveCSS("opacity", "0");
  await group.locator('[data-slot=macro-group-heading]').hover();
  await expect(count).toHaveCSS("opacity", "0");
  await expect(sort).toHaveCSS("opacity", "1");
  await entry.hover();
  await expect(actions).toHaveCSS("opacity", "1");
  // Keyboard focus reveals the same controls without changing their geometry.
  await page.mouse.move(1400, 900);
  await entry.locator('[data-slot=macro-entry]').focus();
  await expect(actions).toHaveCSS("opacity", "1");
}

async function toolCopyControl(page: Page, reduced = false) {
  // A new document keeps open()'s synthetic-response assertion meaningful.
  await open(page, `/?motion-stages=session#tasks/${macro}/preview-run-a1/7?tab=session`);
  const call = page.locator('[data-session-tool="tool:tool-long"]');
  const trigger = call.locator('[data-slot=trace-collapsible-trigger]');
  await trigger.click();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  const output = call.locator('[data-slot=session-output]').first();
  await expect(output).toBeVisible();
  await expect(page.locator('[data-slot=session-loading-earlier]')).toHaveCount(0);
  await settle(page);
  await output.scrollIntoViewIfNeeded();
  const actions = output.locator('[data-slot=code-block-actions]');
  await expectOpacityTransition(actions, reduced);
  await page.mouse.move(5, 5);
  await expect(actions).toHaveCSS("opacity", "0");
  await output.hover();
  await expect(actions).toHaveCSS("opacity", "1");
  await page.mouse.move(5, 5);
  await actions.getByRole("button").focus();
  await expect(actions).toHaveCSS("opacity", "1");
}

/** Preview-only motion contract, also callable by a focused browser probe. */
export async function runCheck(page: Page) {
  await open(page, `#tasks/${macro}`);
  await expect(page.locator('[data-slot=timeline-canvas]')).toHaveCSS("animation-name", "stage-fade-in");
  await settle(page);
  await listControls(page);
  await row(page, "preview-run-a1").click();
  await expect(detail(page)).toHaveCSS("animation-name", "detail-stage-in");
  await expect(detail(page)).toHaveCSS("animation-duration", "0.17s");
  await expect(page.locator('[data-slot=detail-strip]')).toBeVisible();
  // A first step rides with the whole stage, without another translating pane.
  await expect(content(page)).not.toHaveAttribute("data-step-direction");
  await page.locator('[data-slot=detail-strip]').getByRole("button", { name: "Previous step", exact: true }).click();
  await expect(content(page)).toHaveAttribute("data-step-direction", "prev");
  await expect(content(page)).toHaveCSS("animation-name", "step-prev-in");
  for (const [key, direction] of [["ArrowRight", "next"], ["ArrowLeft", "prev"], ["ArrowRight", "next"], ["ArrowLeft", "prev"]]) {
    await page.keyboard.press(key);
    await expect(content(page)).toHaveAttribute("data-step-direction", direction);
    await expect(content(page)).toHaveCSS("animation-name", `step-${direction}-in`);
  }
  // A cross-step click uses a fade, then a different micro task replays the stage.
  await page.locator('[data-slot=detail-strip] [data-key=delegation]').click();
  await expect(content(page)).toHaveAttribute("data-step-direction", "jump");
  await expect(content(page)).toHaveCSS("animation-name", "stage-fade-in");
  await row(page, "preview-run-a2").click();
  await expect(detail(page)).toHaveCSS("animation-name", "detail-stage-in");
  await expect(page.locator('[data-slot=detail-strip]')).toBeVisible();
  await expect(content(page)).not.toHaveAttribute("data-step-direction");
  await toolCopyControl(page);
}

test.describe("stage and control motion", () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 1000 }, reducedMotion: "no-preference" });
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
  });
  test("enters each detail and follows consecutive step selections", async ({ page }) => runCheck(page));

  test("removes stage, step and hover motion when requested", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await open(page, `#tasks/${macro}`);
    await listControls(page, true);
    await row(page, "preview-run-a1").click();
    await expect(page.locator('[data-slot=detail-strip]')).toBeVisible();
    const expectStill = async (target: Locator) => {
      await expect(target).toHaveCSS("animation-name", "none");
      await expect(target).toHaveCSS("transition-property", "none");
      expect(await target.evaluate(node => node.getAnimations().length)).toBe(0);
    };
    await expectStill(detail(page));
    for (const [key, direction] of [["ArrowLeft", "prev"], ["ArrowRight", "next"]]) {
      await page.keyboard.press(key);
      await expect(content(page)).toHaveAttribute("data-step-direction", direction);
      await expectStill(content(page));
      await expectStill(detail(page));
    }
    expect(await detail(page).evaluate(node => node.getAnimations({ subtree: true }).length)).toBe(0);
    await toolCopyControl(page, true);
  });
});
