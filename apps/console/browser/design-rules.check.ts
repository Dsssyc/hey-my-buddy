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
        const more = page.getByRole("button", { name: "更多", exact: true });
        await more.hover();
        await expect(page.locator('[data-slot="tooltip-content"]')).toHaveCSS("border-radius", "6px");
        await more.click();
        await expect(page.locator('[data-slot="popover-content"]')).toHaveCSS("border-radius", "6px");
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
        const size = parseFloat(css.fontSize);
        if ([12, 14, 16, 18].includes(size) ||
            size === 13 && node.closest('[data-slot="macro-entry"]') ||
            size === 12.5 && node.closest('[data-slot="macro-group-more"]')) return [];
        return [`${node.tagName}: ${css.fontSize}`];
      }));
      expect(violations, "shared type sizes, with 13px macro entries and 12.5px group paging only inside those controls").toEqual([]);
      await expect(page.locator("[title]")).toHaveCount(0);
      const heights = await page.locator('[data-slot="button"], [data-slot="input"], [data-slot="select-trigger"], [data-slot="segmented"]').evaluateAll(nodes => nodes
        .filter(node => node.getClientRects().length)
        .map(node => ({ text: node.textContent?.trim(), height: node.getBoundingClientRect().height }))
        .filter(({ height }) => height !== 28 && height !== 32));
      expect(heights, "controls use the two shared heights").toEqual([]);
      const structuralRadii = await page.locator('[data-slot="macro-list"], [data-slot="timeline-tracks"], [data-slot="task-detail"], [data-slot="settings-page"]').evaluateAll(nodes => nodes
        .filter(node => node.getClientRects().length)
        .map(node => ({ slot: node.getAttribute("data-slot"), radius: getComputedStyle(node).borderRadius }))
        .filter(({ radius }) => radius !== "0px"));
      expect(structuralRadii, "page structures remain square").toEqual([]);
      const floatingRadii = await page.locator(".overlay-surface").evaluateAll(nodes => nodes
        .filter(node => node.getClientRects().length)
        .map(node => ({ slot: node.getAttribute("data-slot"), radius: getComputedStyle(node).borderRadius })));
      expect(floatingRadii.every(({ radius }) => radius === "6px"), "floating surfaces share the overlay radius").toBe(true);
      if (view.name === "dialog") expect(floatingRadii.some(({ slot }) => slot === "dialog-content")).toBe(true);
    });
  });
}
