import { expect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { expectNoSidewaysScroll, open, settle, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const shots = "../../tmp/f2-integration-shots";
mkdirSync(shots, { recursive: true });
const tracks = (page: Page) => page.locator('[data-slot="timeline-tracks"]');
const canvas = (page: Page) => page.locator('[data-slot="timeline-canvas"]');
const list = (page: Page) => page.locator('[data-slot="chronological-list"]');

async function openMacro(page: Page, theme: string) {
  await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
  await open(page, `#tasks/${macro}`);
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
  await expect(canvas(page)).toHaveCount(1);
}

/** Keep real event identities and step bindings while forcing two marks to coincide. */
async function clusterFixture(page: Page) {
  await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
    const response = await route.fetch();
    const timeline = await response.json() as MacroTaskTimeline;
    const decision = timeline.events.find(event => event.runId === "preview-run-a2" && event.kind === "decide")!;
    const continuation = timeline.events.find(event => event.runId === "preview-run-a2" && event.kind === "continue")!;
    continuation.at = decision.at;
    await route.fulfill({ response, json: timeline });
  });
}

/** Put a second local-calendar day into the list without changing the preview server. */
async function twoDayFixture(page: Page) {
  await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
    const response = await route.fetch();
    const timeline = await response.json() as MacroTaskTimeline;
    const first = timeline.events[0]!;
    const earliest = Math.min(...timeline.spans.map(span => Date.parse(span.startAt!)).filter(Number.isFinite));
    timeline.events.push({ ...first, seq: Math.max(...timeline.events.map(event => event.seq)) + 1,
      at: new Date(earliest - 86_400_000).toISOString(), kind: "dispatch", summary: "Earlier synthetic dispatch" });
    timeline.totals.events = timeline.events.length;
    await route.fulfill({ response, json: timeline });
  });
}

for (const locale of ["zh-CN", "en-US"] as const) for (const theme of ["light", "dark"] as const) for (const width of [1440, 390]) {
  test.describe(`timeline F2 integration ${locale} ${theme} ${width}px`, () => {
    test.use({ locale, viewport: { width, height: width === 390 ? 844 : 1000 }, reducedMotion: "reduce" });

    test("zooms time twice while the axis and tracks scroll together and names stay fixed", async ({ page }) => {
      await openMacro(page, theme);
      const zoomIn = page.getByRole("button", { name: locale === "zh-CN" ? "放大（+）" : "Zoom in (+)", exact: true });
      const zoomOut = page.getByRole("button", { name: locale === "zh-CN" ? "缩小（-）" : "Zoom out (-)", exact: true });
      const fit = page.getByRole("button", { name: locale === "zh-CN" ? "适应窗口（0）" : "Fit to window (0)", exact: true });
      await expect(zoomIn).toBeVisible(); await expect(zoomOut).toBeVisible(); await expect(fit).toBeVisible();
      const initial = Number(await canvas(page).getAttribute("data-ppm"));
      expect(initial).toBeGreaterThan(0);
      await zoomIn.click();
      await expect.poll(async () => Number(await canvas(page).getAttribute("data-ppm"))).toBeGreaterThan(initial);
      const once = Number(await canvas(page).getAttribute("data-ppm"));
      await zoomIn.click();
      await expect.poll(async () => Number(await canvas(page).getAttribute("data-ppm"))).toBeGreaterThan(once);
      const twice = Number(await canvas(page).getAttribute("data-ppm"));
      expect(await tracks(page).evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
      await expectNoSidewaysScroll(page);
      if (locale === "zh-CN" && theme === "light" && width === 1440)
        await page.screenshot({ path: `${shots}/zoom-two-levels.png` });

      await tracks(page).evaluate(node => { node.scrollLeft = 0; });
      await settle(page);
      const name = page.locator('[data-slot="timeline-row-name"]:visible').first();
      const beforeName = await name.boundingBox();
      const beforeAxis = await page.locator('[data-slot="timeline-axis"]').boundingBox();
      const beforeTrack = await page.locator('[data-slot="timeline-track"]').first().boundingBox();
      await tracks(page).evaluate(node => { node.scrollLeft = Math.min(200, node.scrollWidth - node.clientWidth); });
      await settle(page);
      const afterName = await name.boundingBox();
      const afterAxis = await page.locator('[data-slot="timeline-axis"]').boundingBox();
      const afterTrack = await page.locator('[data-slot="timeline-track"]').first().boundingBox();
      expect(afterAxis!.x).toBeLessThan(beforeAxis!.x);
      expect(afterAxis!.x - beforeAxis!.x).toBeCloseTo(afterTrack!.x - beforeTrack!.x, 0);
      expect(afterName!.x).toBeCloseTo(beforeName!.x, 0);
      await zoomOut.click();
      await expect.poll(async () => Number(await canvas(page).getAttribute("data-ppm"))).toBeLessThan(twice);
      await fit.click();
      await expect.poll(async () => Number(await canvas(page).getAttribute("data-ppm"))).toBeCloseTo(initial, 5);
      const selected = page.locator('[data-slot="timeline-row-name"][data-run-id="preview-run-a2"]:visible');
      await selected.click();
      await expect(selected).toHaveAttribute("aria-expanded", "true");
      await selected.click();
      await expect(selected).toHaveAttribute("aria-expanded", "false");
      const keys = await page.locator('[data-slot="timeline-row"][data-run-id="preview-run-a2"] [data-slot="timeline-track"] [data-key]').evaluateAll(elements => elements.sort((a, b) => Number(a.getAttribute("data-at")) - Number(b.getAttribute("data-at"))).map(element => element.getAttribute("data-key")!));
      const anchored = page.locator(`[data-slot="timeline-track"] [data-key="${keys[0]}"]`);
      const beforeAnchor = await anchored.boundingBox();
      await zoomIn.click();
      const afterAnchor = await anchored.boundingBox();
      expect(Math.abs(afterAnchor!.x - beforeAnchor!.x)).toBeLessThanOrEqual(1);
      await expectNoSidewaysScroll(page);
    });

    test("keeps clustered events in bounds, navigable, and bound to their exact step", async ({ page }) => {
      await clusterFixture(page);
      await openMacro(page, theme);
      const cluster = page.locator('[data-slot="timeline-row"][data-run-id="preview-run-a2"] [data-slot="event-cluster"]');
      await expect(cluster).toHaveCount(1);
      await cluster.scrollIntoViewIfNeeded();
      await cluster.click();
      const popover = page.locator('[data-slot="popover-content"]');
      await expect(popover).toBeVisible();
      const bounds = await tracks(page).boundingBox();
      const bubble = await popover.boundingBox();
      expect(bubble!.x).toBeGreaterThanOrEqual(bounds!.x - 1);
      expect(bubble!.x + bubble!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width + 1);
      expect(bubble!.y).toBeGreaterThanOrEqual(bounds!.y - 1);
      expect(bubble!.y + bubble!.height).toBeLessThanOrEqual(bounds!.y + bounds!.height + 1);
      const choices = popover.getByRole("button");
      await expect(choices).toHaveCount(2);
      await expect(choices.first()).toBeFocused();
      await page.keyboard.press("ArrowDown");
      await expect(choices.nth(1)).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(popover).toHaveCount(0);
      await expect(cluster).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(popover).toBeVisible();
      if (locale === "zh-CN" && theme === "light" && width === 1440)
        await page.screenshot({ path: `${shots}/open-popover.png` });
      await page.keyboard.press("ArrowDown");
      await expect(choices.nth(1)).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(new RegExp(`#tasks/${macro}/preview-run-a2/5$`));
      await expect(page.locator('[data-slot="detail-rail"] [aria-current="step"]')).toContainText(locale === "zh-CN" ? "继续" : "Continue");
      await expectNoSidewaysScroll(page);
    });

    test("shows ordered day groups and idle breaks in the remembered list view", async ({ page }) => {
      await twoDayFixture(page);
      await openMacro(page, theme);
      const switcher = page.getByRole("radio", { name: locale === "zh-CN" ? "清单" : "List", exact: true });
      await switcher.click();
      await expect(switcher).toHaveAttribute("aria-checked", "true");
      await expect(page.locator('[data-slot="timeline-list-stage"]')).toHaveAttribute("aria-hidden", "false");
      await expect(list(page)).toBeVisible();
      const result = await list(page).evaluate(node => {
        const entries = [...node.querySelectorAll<HTMLElement>('[data-slot="chronological-item"], [data-slot="chronological-break"]')];
        const times = entries.map(entry => entry.querySelector("time")?.dateTime).map(value => Date.parse(value ?? ""));
        const headings = [...node.querySelectorAll("h3")];
        return { count: entries.length, times, headings: headings.length,
          breaks: node.querySelectorAll('[data-slot="chronological-break"]').length,
          everyHeadingPrecedesEntry: headings.every(heading => heading.nextElementSibling?.matches('[data-slot="chronological-item"], [data-slot="chronological-break"]')) };
      });
      expect(result.count).toBeGreaterThan(3);
      expect(result.times.every(Number.isFinite)).toBe(true);
      expect(result.times).toEqual([...result.times].sort((a, b) => a - b));
      expect(result.headings).toBeGreaterThanOrEqual(2);
      expect(result.everyHeadingPrecedesEntry).toBe(true);
      expect(result.breaks).toBeGreaterThan(0);
      await expectNoSidewaysScroll(page);
      if (locale === "zh-CN" && width === 1440)
        await page.screenshot({ path: `${shots}/list-${theme}.png` });
      if (locale === "zh-CN" && theme === "light" && width === 390)
        await page.screenshot({ path: `${shots}/list-390.png` });
      await page.reload();
      await expect(page.getByRole("radio", { name: locale === "zh-CN" ? "清单" : "List", exact: true })).toHaveAttribute("aria-checked", "true");
      await expect(list(page)).toBeVisible();
    });
  });
}
