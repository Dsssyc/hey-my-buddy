import { expect } from "@playwright/test";
import { fixtureCoverage } from "../src/session-view/test-fixtures";
import { expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, settle, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const otherMacro = "macro-22222222-2222-4222-8222-222222222222";
const run = "preview-run-a1";

for (const [locale, width, reducedMotion, session, overview, hide, next, routeTitle, turnTitle] of [
  ["en-US", 1440, "no-preference", "Session", "Overview", "Hide timeline", "Next step", "Route", "Turn 1"],
  ["zh-CN", 1440, "reduce", "会话", "概述", "收起时间轴", "下一步", "路由", "回合 1"],
  ["zh-CN", 700, "reduce", "会话", "概述", "收起时间轴", "下一步", "路由", "回合 1"],
] as const) test.describe(`compact Session steps (${locale}, ${width}px)`, () => {
  test.use({ locale, reducedMotion, viewport: { width, height: 960 } });
  test("anchors the controls across steps and opens the real zoomable timeline by click or wheel", async ({ page }) => {
    await page.route("**/api/tasks/*/attempts/*/session?*", route => route.fulfill({
      json: { state: "ended", stopped: null, coverage: fixtureCoverage, events: [], before: 0, after: 0, hasEarlier: false },
    }));
    await open(page, `#tasks/${macro}/${run}/2?tab=session`);
    const bar = page.locator('[data-slot="step-bar"]');
    const toggle = bar.locator('[data-slot="timeline-toggle"]');
    const track = page.locator('[data-slot="detail-timeline"] [data-slot="timeline-track"]');
    const readout = bar.locator('[data-slot="step-readout"]');
    const title = readout.locator(":scope > span").first();
    const panels = page.getByRole("tab", { name: session, exact: true });
    await expect(panels).toHaveAttribute("aria-selected", "true");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(bar).toHaveCSS("height", "38px");
    await expect(toggle).toHaveCSS("height", "38px");
    await expect(toggle).toHaveCSS("border-right-width", "1px");
    await expect(bar.locator('[data-slot="step-navigation"]')).toHaveCSS("border-right-width", "1px");
    await expect(page.locator('[data-slot="session-summary"]')).toHaveCount(0);
    await settle(page);
    const before = (await toggle.boundingBox())!;
    if (width === 1440) {
      await expect(page.locator('[data-slot="macro-list"]')).toBeVisible();
      await expect(page.locator('[data-slot="task-names"]')).toBeVisible();
      const detail = (await page.locator('[data-slot="task-detail"]').boundingBox())!;
      expect(before.width).toBeGreaterThanOrEqual(220);
      expect(before.width).toBeLessThanOrEqual(420);
      expect(before.width).toBeCloseTo(detail.width * 0.32, 0);
      await expectWholeAndApart(readout, readout.locator(":scope > *"));
    }
    await expect(title).toHaveText(routeTitle);
    await expectTextUncut(title);
    await expectWholeAndApart(bar, bar.locator(":scope > *"));
    const thumbnail = toggle.locator('[data-slot="timeline-thumbnail"]');
    await expect(thumbnail.locator('[data-slot="thumbnail-selection"]')).toBeVisible();
    expect(await thumbnail.evaluate(svg => {
      const baseline = svg.querySelector('[data-slot="thumbnail-baseline"]')!;
      return Array.from(svg.querySelectorAll('[data-slot="thumbnail-routing"],[data-slot="thumbnail-execution"]')).every(bar => {
        const style = getComputedStyle(bar);
        return Boolean(baseline.compareDocumentPosition(bar) & Node.DOCUMENT_POSITION_FOLLOWING) &&
          style.fill !== "none" && style.fillOpacity === "1" && style.opacity === "1";
      });
    })).toBe(true);
    await bar.getByRole("button", { name: next, exact: true }).click();
    await expect(page).toHaveURL(/\/3\?tab=session$/);
    const expectUnmovedToggle = async () => {
      const box = (await toggle.boundingBox())!;
      expect(box.x).toBe(before.x);
      expect(box.width).toBe(before.width);
    };
    await expectUnmovedToggle();
    await expect(title).toHaveText(turnTitle);
    await expectTextUncut(title);
    if (width === 1440) await expectWholeAndApart(readout, readout.locator(":scope > *"));
    await toggle.click();
    await expect(toggle).toHaveAccessibleName(hide);
    await settle(page);
    await expectUnmovedToggle();
    const fit = Number(await track.getAttribute("width"));
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await settle(page);
    await expectUnmovedToggle();
    await toggle.hover();
    await page.mouse.wheel(0, -250);
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect.poll(async () => Number(await track.getAttribute("width"))).toBeGreaterThan(fit);
    const ruler = page.locator('[data-slot="detail-timeline"] [data-slot="zoom-ruler"]');
    await ruler.dblclick({ position: { x: 50, y: 15 } });
    await expect.poll(async () => Number(await track.getAttribute("width"))).toBe(fit);
    await toggle.click();
    await page.getByRole("tab", { name: overview, exact: true }).click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expectNoSidewaysScroll(page);
  });
});

test("opens a queued blocker in its own macro task", async ({ page }) => {
  await page.route(`**/api/tasks/${run}`, async route => {
    const response = await route.fetch();
    const task = await response.json();
    await route.fulfill({ response, json: { ...task, state: "queued", queueReason: "cwd-overlap",
      blockingTask: { runId: "preview-run-b1", title: "Prepare checkout", macroTaskId: otherMacro } } });
  });
  await open(page, `#tasks/${macro}/${run}/1`);
  const header = page.locator('[data-slot="detail-header"]');
  await header.getByRole("button", { name: "Prepare checkout" }).click();
  await expect(page).toHaveURL(new RegExp(`/${otherMacro}/preview-run-b1(?:/|$)`));
});
