import { expect, type Page } from "@playwright/test";
import { open, test, settle, expectNoSidewaysScroll, expectWholeAndApart } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const waiting = "preview-run-a1-h1";
const names = (page: Page) => page.locator('[data-slot=task-names]');
const row = (page: Page, id: string) => page.locator(`[data-slot=timeline-row-name][data-run-id="${id}"]:visible`);
const shots = process.env.HMB_TIMELINE_SHOTS;

for (const locale of ["zh-CN", "en-US"]) for (const theme of ["light", "dark"]) for (const width of [1440, 800, 390]) {
  test.describe(`new task structure ${locale} ${theme} ${width}`, () => {
    test.use({ locale, viewport: { width, height: width === 390 ? 844 : 1000 }, reducedMotion: "reduce" });
    test("keeps names, tracks, steps and detail inside their frames", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/${macro}`);
      await page.locator(`[data-macro-task-id="${macro}"]`).waitFor({ state: "attached" });
      await expect(page.locator('[data-slot=timeline-axis]')).toHaveCount(1);
      const visibleNames = page.locator('[data-slot=timeline-row-name]:visible');
      await expect(visibleNames.first()).toBeVisible();
      const selected = row(page, waiting);
      const before = await selected.boundingBox();
      await expectWholeAndApart(selected, selected.locator(':scope > span'));
      await expectNoSidewaysScroll(page);
      const overflow = await page.locator('[data-slot=timeline-tracks]').evaluate(node => node.scrollWidth > node.clientWidth);
      expect(overflow).toBe(false);
      if (shots && locale === "zh-CN" && width !== 800) await page.screenshot({ path: `${shots}/timeline-${theme}-${width}.png` });
      // At the intermediate width the fitted marks can cover a short turn.
      // Zoom once to expose its own pointer target, then exercise the readout.
      if (width === 800) await page.getByRole("button", { name: locale === "zh-CN" ? "放大（+）" : "Zoom in (+)", exact: true }).click();
      const segment = page.locator('[data-key="span:execution:att-a1-2"]');
      await segment.hover();
      await expect(page.locator('[data-slot=timeline-status]')).toContainText(locale === "zh-CN" ? "回合" : "Turn");
      if (shots && locale === "zh-CN" && theme === "light" && width === 1440) await page.screenshot({ path: `${shots}/segment-readout.png` });
      await selected.click();
      await expect(page.locator('[data-slot=task-detail]')).toBeVisible();
      await expect(page.locator('[data-slot=detail-rail]')).toBeVisible();
      await settle(page);
      const after = await row(page, waiting).boundingBox();
      expect(after!.width).toBeCloseTo(before!.width, 0);
      expect(after!.height).toBeCloseTo(before!.height, 0);
      expect(after!.x).toBeCloseTo(before!.x, 0);
      if (width > 760) expect(after!.y).toBeCloseTo(before!.y, 0);
      const selectedColor = await row(page, waiting).evaluate(node => getComputedStyle(node).backgroundColor);
      expect(selectedColor).toBe(theme === "light" ? "rgb(220, 237, 226)" : "rgb(36, 59, 46)");
      await row(page, waiting).hover();
      expect(await row(page, waiting).evaluate(node => getComputedStyle(node).backgroundColor)).toBe(selectedColor);
      await expectWholeAndApart(page.locator('[data-slot=timeline-detail-stage]'), page.locator('[data-slot=task-detail]'));
      await expectNoSidewaysScroll(page);
      if (width <= 760) {
        const panel = await page.locator('[data-slot=task-detail]').boundingBox();
        expect(panel!.y).toBeGreaterThanOrEqual(after!.y + after!.height);
        await expect(visibleNames).toHaveCount(1);
        await names(page).evaluate(node => { node.scrollTop = node.scrollHeight; });
        await expect(row(page, waiting)).toBeInViewport();
        expect((await row(page, waiting).boundingBox())!.y).toBeCloseTo(after!.y, 0);
        await names(page).evaluate(node => { node.scrollTop = 0; });
      }
      if (shots && locale === "zh-CN" && width !== 800) await page.screenshot({ path: `${shots}/detail-${theme}-${width}.png` });
      await row(page, waiting).click();
      await expect(page).toHaveURL(new RegExp(`#tasks/${macro}$`));
      await expect(row(page, waiting)).toBeFocused();
    });
  });
}

for (const reducedMotion of ["reduce", "no-preference"] as const) test.describe(`task transition ${reducedMotion}`, () => {
  test.use({ viewport: { width: 1440, height: 1000 }, reducedMotion });
  test("uses displacement and opacity within 200ms, and stops motion when requested", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    const stage = page.locator('[data-slot=timeline-detail-stage]');
    await expect(row(page, waiting)).toBeVisible();
    const style = await stage.evaluate(node => {
      const css = getComputedStyle(node);
      return { duration: css.transitionDuration, property: css.transitionProperty, translate: css.translate, opacity: css.opacity };
    });
    expect(style.opacity).toBe("0");
    expect(style.translate).not.toBe("none");
    if (reducedMotion === "reduce") expect(style.property).toBe("none");
    else {
      expect(style.property).toContain("opacity"); expect(style.property).toContain("translate");
      expect(parseFloat(style.duration)).toBeGreaterThan(0); expect(parseFloat(style.duration)).toBeLessThanOrEqual(.2);
    }
    const pulse = row(page, "preview-run-a3").locator('[data-slot=row-state] > span');
    if (await pulse.count()) expect(await pulse.evaluate(node => getComputedStyle(node).animationName)).toBe(reducedMotion === "reduce" ? "none" : "ping");
    await row(page, waiting).click();
    await settle(page);
    await expect(stage).toHaveCSS("opacity", "1");
    await expect(stage).toHaveCSS("translate", "0px");
    await page.keyboard.press("Escape");
    await settle(page);
    await expect(stage).toHaveCSS("opacity", "0");
    await expect(row(page, waiting)).toBeFocused();
  });
});

for (const width of [1440, 390]) test.describe(`many breaks at ${width}px`, () => {
  test.use({ viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test("keeps breaks at 54 real pixels and labels in view when the canvas needs more room", async ({ page }) => {
    await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
      const response = await route.fetch();
      const timeline = await response.json();
      const first = timeline.rows[0];
      const example = timeline.spans.find((span: { kind: string }) => span.kind === "execution");
      const base = Date.parse(example.startAt);
      timeline.rows = [{ ...first, state: "accepted", category: "ended", acceptedAt: null, reviewRejectedAt: null }];
      timeline.events = [];
      timeline.spans = Array.from({ length: 30 }, (_, index) => ({ ...example, spanId: `many-${index}`, runId: first.runId,
        startAt: new Date(base + index * 7_200_000).toISOString(), endAt: new Date(base + index * 7_200_000 + 60_000).toISOString(),
        state: "finished", uncertain: false, shutdownConfirmed: true, resultStatus: "ok" }));
      timeline.totals = { rows: 1, spans: 30, events: 0, allRows: 1 };
      timeline.truncated = { rows: false, spans: false, events: false };
      timeline.filtered = false; timeline.scopeComplete = true;
      await route.fulfill({ response, json: timeline });
    });
    await open(page, `#tasks/${macro}`);
    const gap = page.locator('[data-slot=timeline-break]').first();
    await expect(gap).toBeVisible();
    expect((await gap.boundingBox())!.width).toBeCloseTo(54, 0);
    const label = page.locator('[data-slot=timeline-row-name]:visible').first();
    const before = await label.boundingBox();
    await page.locator('[data-slot=timeline-tracks]').evaluate(node => { node.scrollLeft = 300; });
    await settle(page);
    const after = await label.boundingBox();
    expect(after!.x).toBeCloseTo(before!.x, 0);
    expect(after!.width).toBeCloseTo(before!.width, 0);
    await expectNoSidewaysScroll(page);
  });
});


for (const locale of ["zh-CN", "en-US"]) for (const width of [1440, 390]) test.describe(`timeline keyboard ${locale} ${width}`, () => {
  test.use({ locale, viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test("uses one Tab stop, reads each focused fragment, and navigates open tasks and steps", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    const allRows = page.locator('[data-slot=timeline-row-name]:visible');
    const first = allRows.first();
    await expect(first).toHaveAttribute("tabindex", "0");
    await first.focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    await expect(first).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(allRows.nth(1)).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await expect(first).toBeFocused();
    const keyboardRun = "preview-run-a1";
    const selected = row(page, keyboardRun);
    await selected.focus();
    const keys = await page.locator(`[data-slot=timeline-row][data-run-id="${keyboardRun}"] [data-slot=timeline-track] [data-key]`).evaluateAll(elements => elements
      .sort((left, right) => Number(left.getAttribute("data-at")) - Number(right.getAttribute("data-at")))
      .map(element => element.getAttribute("data-key")!));
    const fragment = (key: string) => page.locator(`[data-slot=timeline-track] [data-key="${key}"]`);
    await page.keyboard.press("ArrowRight");
    await expect(fragment(keys[0])).toBeFocused();
    const readout = await page.locator('[data-slot=timeline-status]').innerText();
    await expect(page.getByRole("button", { name: readout, exact: true })).toBeFocused();
    await expect(fragment(keys[0]).locator('[data-slot=fragment-focus]')).toHaveCSS("opacity", "1");
    await page.screenshot({ path: "../../tmp/f2-integration-shots/fragment-focus.png" });
    await page.keyboard.press("ArrowLeft");
    await expect(selected).toBeFocused();
    await page.keyboard.press("End"); await expect(fragment(keys.at(-1)!)).toBeFocused();
    await page.keyboard.press("Home"); await expect(fragment(keys[0])).toBeFocused();
    const execution = "span:execution:att-a1-2";
    for (let index = 0; index < keys.indexOf(execution); index++) await page.keyboard.press("ArrowRight");
    await expect(fragment(execution)).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(selected).toHaveAttribute("aria-expanded", "true");
    const chosen = page.locator('[data-slot=detail-rail] [aria-current=step]');
    await expect(chosen).toContainText(locale === "zh-CN" ? "回合 2" : "Turn 2");
    const current = Number(await chosen.getAttribute("data-step"));
    await page.keyboard.press("ArrowLeft"); await expect(chosen).toHaveAttribute("data-step", String(current - 1));
    await expect(chosen).toBeFocused();
    await page.keyboard.press("ArrowRight"); await expect(chosen).toHaveAttribute("data-step", String(current));
    await page.keyboard.press("ArrowDown");
    await expect(page.locator(`[data-slot=timeline-row-name][data-run-id="${keyboardRun}"]`)).toHaveAttribute("aria-expanded", "false");
    const adjacent = page.locator('[data-slot=timeline-row-name][aria-expanded=true]');
    await expect(adjacent).toBeFocused();
    await page.keyboard.press("ArrowUp"); await expect(selected).toHaveAttribute("aria-expanded", "true");
    await page.keyboard.press("Escape");
    await expect(selected).toBeFocused(); await expect(selected).toHaveAttribute("aria-expanded", "false");
    await page.keyboard.press("Tab");
    await expect(page.locator('[data-slot=task-work] [data-key]:focus')).toHaveCount(0);
    await page.keyboard.press("Shift+Tab"); await expect(selected).toBeFocused();
    await expectNoSidewaysScroll(page);
  });
});
