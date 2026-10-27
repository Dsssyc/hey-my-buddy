import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { screenshots as shots, expectNoSidewaysScroll, open, settle, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const views = [
  { name: "tasks", hash: `#tasks/${macro}`, ready: "[data-slot=timeline-tracks]" },
  { name: "detail", hash: `#tasks/${macro}/preview-run-a3/3`, ready: "[data-slot=task-detail]" },
  { name: "buddies", hash: "#buddies", ready: "[data-slot=catalogue-controls]" },
  { name: "settings", hash: "#settings", ready: "[data-slot=settings-page]" },
  { name: "drawer", hash: "#buddies", ready: "[data-slot=catalogue-controls]" },
  { name: "dialog", hash: `#tasks/${macro}`, ready: "[data-slot=timeline-tracks]" },
];

for (const width of [1280, 390]) for (const theme of ["light", "dark"] as const) {
  test.describe(`shared design rules at ${width}px in ${theme}`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 900 }, colorScheme: theme, reducedMotion: "reduce" });
    for (const view of views) test(view.name, async ({ page }) => {
      await page.clock.setFixedTime(new Date("2026-09-27T10:00:00.000Z"));
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, view.hash);
      await page.locator(view.ready).first().waitFor();
      if (view.name === "drawer") await page.getByRole("button", { name: "codex / openai / gpt-5-codex", exact: true }).click();
      if (view.name === "dialog") {
        await page.getByRole("button", { name: "更多", exact: true }).click();
        await page.getByRole("button", { name: "停止宏任务", exact: true }).click();
        await page.getByRole("dialog").waitFor();
      }
      await settle(page);
      mkdirSync(shots, { recursive: true });
      await page.screenshot({ path: join(shots, `${view.name}-${width}-${theme}.png`), fullPage: true, animations: "disabled" });
      await expectNoSidewaysScroll(page);
      // This uses the built CSS: inheritance and the reset matter as much as
      // the classes a component declares. Hidden rulers are excluded.
      const violations = await page.locator("body *").evaluateAll(nodes => nodes.flatMap(node => {
        if (!(node instanceof HTMLElement || node instanceof SVGElement) || !node.getClientRects().length) return [];
        const css = getComputedStyle(node);
        if (css.visibility === "hidden" || !node.textContent?.trim()) return [];
        return [12, 14, 16, 18].includes(parseFloat(css.fontSize)) ? [] : [`${node.tagName}: ${css.fontSize}`];
      }));
      expect(violations, "only the four type sizes are drawn").toEqual([]);
      await expect(page.locator("[title]")).toHaveCount(0);
      const heights = await page.locator('[data-slot="button"], [data-slot="input"], [data-slot="select-trigger"], [data-slot="segmented"]').evaluateAll(nodes => nodes
        .filter(node => node.getClientRects().length)
        .map(node => ({ text: node.textContent?.trim(), height: node.getBoundingClientRect().height }))
        .filter(({ height }) => height !== 28 && height !== 32));
      expect(heights, "controls use the two shared heights").toEqual([]);
    });
  });
}
