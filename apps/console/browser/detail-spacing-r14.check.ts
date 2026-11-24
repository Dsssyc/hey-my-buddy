import { expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expandDetailTimeline, open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const captures = fileURLToPath(new URL("../test-results/shots/detail-groups", import.meta.url));
for (const theme of ["dark", "light"]) for (const width of [1440, 390]) test.describe(`detail groups ${theme} ${width}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test("groups the strip into a paper band beside the ruled header bar", async ({ page }) => {
    await page.addInitScript(theme => localStorage.setItem("hey-my-buddy.console.theme", theme), theme);
    await open(page, `#tasks/${macro}/preview-run-a1/7`);
    const header = page.locator('[data-slot=detail-header]');
    const strip = page.locator('[data-slot=detail-strip]');
    const band = page.locator('[data-slot=detail-step-band]');
    const ruler = strip.locator('[data-slot=zoom-ruler]');
    const tabs = page.locator('[data-slot=detail-tabs] [role=tablist]');
    const title = page.locator('[data-slot=detail-pane] h3').first();
    await expect(title).toHaveText("回合 2");
    const toggle = strip.locator('[data-slot=timeline-toggle]');
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(toggle.locator('[data-slot=timeline-thumbnail]')).toBeVisible();
    await expect(strip).toHaveCSS("height", "38px");
    await expandDetailTimeline(page);
    const [headBox, stripBox, tabsBox, titleBox] = await Promise.all([header.boundingBox(), strip.boundingBox(), tabs.boundingBox(), title.boundingBox()]);
    expect(stripBox!.y - headBox!.y - headBox!.height).toBeCloseTo(0, 0);
    expect(titleBox!.y).toBeGreaterThan(stripBox!.y + stripBox!.height);
    expect(tabsBox!.y).toBeGreaterThan(titleBox!.y + titleBox!.height);
    await expect(strip).toHaveCSS("border-bottom-width", "0px");
    await expect(tabs).toHaveCSS("height", "34px");
    await expect(tabs).toHaveCSS("border-bottom-width", "1px");
    const [paper, sunk, line] = await page.evaluate(() => {
      const probe = document.body.appendChild(document.createElement("span"));
      const colors = ["--paper", "--sunk", "--line"].map(token => {
        probe.style.backgroundColor = `var(${token})`;
        return getComputedStyle(probe).backgroundColor;
      });
      probe.remove();
      return colors;
    });
    const timeline = strip.locator('[data-slot=detail-timeline]');
    await expect(timeline).toHaveCSS("border-top-color", line);
    await expect(timeline).toHaveCSS("background-color", paper);
    await expect(ruler).toHaveCSS("border-bottom-color", line);
    await expect(ruler).toHaveCSS("background-color", sunk);
    await expect(band).toHaveCSS("background-color", paper);
    await expect(band).toHaveCSS("border-bottom-width", "1px");
    // The workspace is the page's own ground, and the header bar carries the ruler's.
    expect(await header.evaluate(node => getComputedStyle(node).backgroundColor)).not.toBe(paper);
    await expect(page.locator('[data-slot=task-detail]')).toHaveCSS("background-color", paper);
    await expect(page.locator('[data-slot=detail-pane]')).toHaveCSS("background-color", paper);
    const bandBox = (await band.boundingBox())!, detailBox = (await page.locator('[data-slot=task-detail]').boundingBox())!;
    expect(Math.abs(bandBox.x - detailBox.x)).toBeLessThanOrEqual(1);
    expect(Math.abs(bandBox.width - detailBox.width)).toBeLessThanOrEqual(1);
    const selected = tabs.getByRole("tab", { name: "概述", exact: true });
    expect(Math.abs((await selected.boundingBox())!.x - titleBox!.x)).toBeLessThanOrEqual(1);
    await expect(selected).toHaveCSS("font-size", "12px");
    await expect(selected).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    const underline = await selected.evaluate(node => ({ height: getComputedStyle(node, "::after").height, opacity: getComputedStyle(node, "::after").opacity }));
    expect(underline).toEqual({ height: "2px", opacity: "1" });
    const axis = ruler.locator('[data-slot=timeline-axis]');
    const clocks = axis.locator('[data-slot=axis-label]');
    expect(await clocks.count()).toBeLessThanOrEqual(Math.ceil((await axis.boundingBox())!.width / 160) + 2);
    await mkdir(captures, { recursive: true });
    await page.mouse.move(0, 0); await page.screenshot({ path: `${captures}/overview-${theme}-${width}.png` });
    await tabs.getByRole("tab", { name: "会话", exact: true }).click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    // No header above the session: the transcript begins under the tab row, where
    // the session coverage control sits at the right end.
    const coverage = tabs.getByRole("button", { name: "会话内容", exact: true });
    await expect(coverage).toBeVisible();
    const viewport = page.locator('[data-slot=session-view] [data-slot=scroll-area-viewport]');
    await expect(viewport).toBeVisible();
    expect(await viewport.evaluate((node, list) => node.getBoundingClientRect().top - list.getBoundingClientRect().bottom, await tabs.elementHandle())).toBeLessThan(20);
    await page.screenshot({ path: `${captures}/session-${theme}-${width}.png` });
  });
});
