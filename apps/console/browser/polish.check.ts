import { expect } from "@playwright/test";
import { expectNoSidewaysScroll, open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const shots = "../../tmp/polish-shots";
for (const theme of ["light", "dark"]) for (const width of [1440, 390]) test.describe(`trial polish ${theme} ${width}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: width === 390 ? 844 : 1000 }, reducedMotion: "reduce" });
  test("selects without opening, opens exact turns, collapses with the back button and switches details", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, `#tasks/${macro}`);
    const row = page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a1"]:visible');
    await row.click();
    await expect(row).toHaveAttribute("aria-expanded", "false");
    await expect(page).toHaveURL(new RegExp(`#tasks/${macro}$`));
    await expect(row).toHaveCSS("background-color", theme === "dark" ? "rgb(29, 59, 43)" : "rgb(220, 237, 226)");
    await expect(page.locator('[data-slot=timeline-row][data-run-id="preview-run-a1"] [data-slot=selected-span]').first()).toBeAttached();
    await row.hover();
    await expect(row).toHaveCSS("background-color", theme === "dark" ? "rgb(29, 59, 43)" : "rgb(220, 237, 226)");
    await page.screenshot({ path: `${shots}/selected-${theme}-${width}.png` });
    const edges = await page.locator('[data-slot=timeline-row-name]:visible').evaluateAll(nodes => nodes.map(node => {
      const css = getComputedStyle(node);
      return { tone: node.getAttribute("data-state-tone"), shadow: css.boxShadow };
    }));
    expect(edges.length).toBeGreaterThan(0);
    for (const edge of edges) expect(edge.shadow).toMatch(edge.tone === "queued" ? /^none$/ : /3px/);
    await expect(page.getByRole("radio", { name: "清单", exact: true })).toHaveCount(0);
    await row.dblclick();
    await expect(page.locator('[data-slot=detail-rail]')).toBeVisible();
    await page.screenshot({ path: `${shots}/micro-task-${theme}-${width}.png` });
    if (width > 760) {
      const other = page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a2"]:visible');
      await other.click();
      await expect(other).toHaveAttribute("aria-expanded", "true");
    }
    await page.locator('[data-slot=detail-header]').getByRole("button", { name: "收起详情" }).click();
    await expect(page.locator('[data-slot=task-work]')).toHaveAttribute("data-mode", "timeline");
    const focused = page.locator('[data-slot=timeline-row-name]:focus');
    await expect(focused).toHaveCount(1);
    await page.keyboard.press("Enter");
    await expect(page.locator('[data-slot=detail-rail]')).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator('[data-slot=task-work]')).toHaveAttribute("data-mode", "timeline");
    await expectNoSidewaysScroll(page);
  });

  test("reads idle intervals and clock minutes or seconds at the pointer, and highlights the exact inquiry", async ({ page }) => {
    // The service's inquiry identity and stored-message identity are independent.
    await page.route("**/api/command", async route => {
      const response = await route.fetch();
      if (route.request().postDataJSON().operation !== "message_list") { await route.fulfill({ response }); return; }
      const data = await response.json();
      for (const message of data.result.messages) message.messageId = `stored-${message.inquiryId}`;
      await route.fulfill({ response, json: data });
    });
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, `#tasks/${macro}`);
    const axis = page.locator('[data-slot=timeline-axis]');
    await expect(axis).toBeVisible();
    let box = (await axis.boundingBox())!;
    await page.mouse.move(box.x + 28, box.y + 15);
    await expect(page.locator('[data-slot=pointer-clock]')).toHaveText(/^\d{2}:\d{2}$/);
    await page.screenshot({ path: `${shots}/pointer-minutes-${theme}-${width}.png` });
    const zoom = page.getByRole("button", { name: "放大（+）", exact: true });
    for (let i = 0; i < 12 && Number(await page.locator('[data-slot=timeline-canvas]').getAttribute("data-ppm")) <= 30; i++) await zoom.click();
    box = (await axis.boundingBox())!;
    const viewport = (await page.locator('[data-slot=timeline-tracks]').boundingBox())!;
    await page.mouse.move(viewport.x + 40, box.y + 15);
    await expect(page.locator('[data-slot=pointer-clock]')).toHaveText(/^\d{2}:\d{2}:\d{2}$/);
    await page.mouse.move(viewport.x + viewport.width - 20, box.y + 15);
    const clockBox = (await page.locator('[data-slot=pointer-clock]').boundingBox())!;
    expect(clockBox.x + clockBox.width).toBeLessThanOrEqual(viewport.x + viewport.width);
    await page.screenshot({ path: `${shots}/pointer-seconds-${theme}-${width}.png` });
    await page.getByRole("button", { name: "适应窗口（0）", exact: true }).click();
    const gap = page.locator('[data-slot=timeline-break]').first();
    if (await gap.count()) {
      await gap.scrollIntoViewIfNeeded();
      const gapBox = (await gap.boundingBox())!;
      await page.mouse.move(gapBox.x + gapBox.width / 2, gapBox.y + 8);
      await expect(page.locator('[data-slot=timeline-status]')).toContainText(/^空闲 \d{2}:\d{2}–\d{2}:\d{2} · /);
      expect(await gap.getAttribute("fill")).toMatch(/^url\(#/);
      await page.screenshot({ path: `${shots}/idle-${theme}-${width}.png` });
    }
    const inquiry = page.locator('[data-key="inquiry:inq-a3-1"]');
    await inquiry.scrollIntoViewIfNeeded();
    await inquiry.click();
    const highlighted = page.locator('[data-inquiry-id="inq-a3-1"][data-highlighted=true]');
    await expect(highlighted).toBeInViewport();
    await expect(page.locator('[data-slot=detail-rail] [aria-current=step]')).toContainText("回合 1");
    await page.screenshot({ path: `${shots}/inquiry-${theme}-${width}.png` });
    await expectNoSidewaysScroll(page);
  });
});
