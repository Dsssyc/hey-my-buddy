import { expect, type Locator } from "@playwright/test";
import { screenshots as shots, expectNoSidewaysScroll, open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";

async function hoverExecutionBody(slice: Locator) {
  // Inquiries own their marks above the execution. Aim inside the bar's upper
  // body so its hover checks the execution instead of the group's shared centre.
  const bar = slice.locator('[data-slot=execution-bar]');
  await expect(bar).toBeVisible();
  const box = (await bar.boundingBox())!;
  await bar.hover({ position: { x: box.width / 2, y: box.height / 8 } });
}

for (const theme of ["light", "dark"]) for (const width of [1440, 390]) test.describe(`trial polish ${theme} ${width}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: width === 390 ? 844 : 1000 }, reducedMotion: "reduce" });
  test("opens a micro task with one click, frames spans on hover, and switches details", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, `#tasks/${macro}`);
    const row = page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a1"]:visible');
    await row.click();
    await expect(page.locator('[data-slot=detail-strip]')).toBeVisible();
    await page.locator('[data-slot=detail-header]').getByRole("button", { name: "收起详情" }).click();
    await expect(page.locator('[data-slot=task-work]')).toHaveAttribute("data-mode", "timeline");
    await expect(page.locator('[data-slot=timeline-tracks]')).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`#tasks/${macro}$`));
    await expect(row).toHaveCSS("background-color", theme === "dark" ? "rgba(255, 255, 255, 0.05)" : "rgba(0, 0, 0, 0.04)");
    await expect(page.locator('[data-slot=timeline-row][data-run-id="preview-run-a1"] [data-slot=selected-row]')).toBeAttached();
    await expect(page.locator('[data-slot=timeline-row][data-run-id="preview-run-a1"] [data-slot=selected-item]')).toHaveCount(0);
    if (width === 390) await page.locator('[data-slot=zoom-ruler]:visible').press("+");
    const slice = page.locator('[data-slot=timeline-row][data-run-id="preview-run-a1"] [data-key="span:execution:att-a1-1"]');
    await hoverExecutionBody(slice);
    await expect(slice.locator('[data-slot=selected-item]')).toBeAttached();
    await expect(page.locator('[data-slot=task-work]')).toHaveAttribute("data-mode", "timeline");
    await expect(page).toHaveURL(new RegExp(`#tasks/${macro}$`));
    await row.hover();
    await expect(row).toHaveCSS("background-color", theme === "dark" ? "rgba(255, 255, 255, 0.05)" : "rgba(0, 0, 0, 0.04)");
    await page.screenshot({ path: `${shots}/selected-${theme}-${width}.png` });
    const edges = await page.locator('[data-slot=timeline-row-name]:visible').evaluateAll(nodes => nodes.map(node => {
      return { tone: node.getAttribute("data-state-tone"), width: node.querySelector('[data-slot=row-state-edge]')?.getBoundingClientRect().width ?? 0 };
    }));
    expect(edges.length).toBeGreaterThan(0);
    for (const edge of edges) expect(edge.width).toBe(edge.tone === "queued" ? 0 : 3);
    await page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a1"]:visible').click();
    await expect(page.locator('[data-slot=detail-strip]')).toBeVisible();
    await page.screenshot({ path: `${shots}/micro-task-${theme}-${width}.png` });
    if (width > 760) {
      // The list beside the detail switches micro tasks with one click; the open row is where the reader is.
      const other = page.locator('[data-slot=task-names] [data-slot=timeline-row-name][data-run-id="preview-run-a2"]');
      await other.click();
      await expect(other).toHaveAttribute("aria-current", "true");
    }
    await page.locator('[data-slot=detail-header]').getByRole("button", { name: "收起详情" }).click();
    await expect(page.locator('[data-slot=task-work]')).toHaveAttribute("data-mode", "timeline");
    const focused = page.locator('[data-slot=timeline-row-name]:focus');
    await expect(focused).toHaveCount(1);
    await page.keyboard.press("Enter");
    await expect(page.locator('[data-slot=detail-strip]')).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator('[data-slot=task-work]')).toHaveAttribute("data-mode", "timeline");
    await expectNoSidewaysScroll(page);
  });

  test("keeps execution and inquiry hover reachable after the preview clock advances", async ({ page }) => {
    // The preview's Date advances with server uptime. At this age the narrow
    // fitted scale puts the execution group's centre on the inquiry's hit area.
    await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
      const response = await route.fetch();
      const timeline = await response.json();
      await route.fulfill({ response, headers: { ...response.headers(), date: new Date(Date.parse(timeline.observedAt) + 140_000).toUTCString() } });
    });
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, `#tasks/${macro}`);
    await page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a1"]:visible').click();
    await expect(page.locator('[data-slot=detail-strip]')).toBeVisible();
    await page.locator('[data-slot=detail-header]').getByRole("button", { name: "收起详情" }).click();
    if (width === 390) await page.locator('[data-slot=zoom-ruler]:visible').press("+");
    const slice = page.locator('[data-slot=timeline-row][data-run-id="preview-run-a1"] [data-key="span:execution:att-a1-1"]');
    await hoverExecutionBody(slice);
    await expect(slice.locator('[data-slot=selected-item]')).toBeAttached();
    const inquiry = page.locator('[data-key="inquiry:inq-a1-1"]');
    await inquiry.hover();
    await expect(inquiry.locator('[data-slot=selected-item]')).toBeAttached();
    await expect(slice.locator('[data-slot=selected-item]')).toHaveCount(0);
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
    const ruler = page.locator('[data-slot=zoom-ruler]:visible');
    for (let i = 0; i < 12 && Number(await page.locator('[data-slot=timeline-canvas]').getAttribute("data-ppm")) <= 30; i++) await ruler.press("+");
    box = (await axis.boundingBox())!;
    const viewport = (await page.locator('[data-slot=timeline-tracks]').boundingBox())!;
    await page.mouse.move(viewport.x + (width > 760 ? 272 : 0) + 40, box.y + 15);
    await expect(page.locator('[data-slot=pointer-clock]')).toHaveText(/^\d{2}:\d{2}:\d{2}$/);
    await page.mouse.move(viewport.x + viewport.width - 20, box.y + 15);
    const clockBox = (await page.locator('[data-slot=pointer-clock]').boundingBox())!;
    expect(clockBox.x + clockBox.width).toBeLessThanOrEqual(viewport.x + viewport.width);
    await page.screenshot({ path: `${shots}/pointer-seconds-${theme}-${width}.png` });
    await ruler.press("0");
    const gap = page.locator('[data-slot=timeline-break]').first();
    if (await gap.count()) {
      await gap.scrollIntoViewIfNeeded();
      const gapBox = (await gap.boundingBox())!;
      await page.mouse.move(gapBox.x + gapBox.width / 2, gapBox.y + 8);
      await expect(page.locator('[data-slot=timeline-status]')).toContainText(/^空闲 \d{2}:\d{2}–\d{2}:\d{2} · /);
      expect(await gap.getAttribute("fill")).toMatch(/^url\(#/);
      await expect(page.locator('[data-slot=pointer-clock]')).toHaveCount(0);
      await page.screenshot({ path: `${shots}/idle-${theme}-${width}.png` });
    }
    const inquiry = page.locator('[data-key="inquiry:inq-a3-1"]');
    await inquiry.scrollIntoViewIfNeeded();
    await inquiry.hover();
    await expect(page.locator('[data-slot=task-work]')).toHaveAttribute("data-mode", "timeline");
    const readout = await inquiry.getAttribute("aria-label");
    await page.mouse.move(5, 5);
    await inquiry.focus();
    await expect(page.locator('[data-slot=timeline-status] [role=status]')).toHaveText(readout!);
    await page.screenshot({ path: `${shots}/inquiry-selected-${theme}-${width}.png` });
    const before = (await inquiry.boundingBox())!.x;
    await page.keyboard.press("+");
    expect(Math.abs((await inquiry.boundingBox())!.x - before)).toBeLessThanOrEqual(1);
    await inquiry.click();
    const highlighted = page.locator('[data-inquiry-id="inq-a3-1"][data-highlighted=true]');
    await expect(highlighted).toBeInViewport();
    await expect(page.locator('[data-slot=detail-strip] [aria-pressed=true]')).toHaveAccessibleName(/回合 1/);
    await page.screenshot({ path: `${shots}/inquiry-${theme}-${width}.png` });
    await expectNoSidewaysScroll(page);
  });

  test("can close a pending or failed first detail read with the same back button", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/api/tasks/preview-run-a3", async route => {
      await pending;
      // An unreadable task response exercises the failure UI without a browser resource error.
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ task: null }) });
    });
    await open(page, `#tasks/${macro}/preview-run-a3`);
    const back = page.locator('[data-slot=detail-header]').getByRole("button", { name: "收起详情" });
    await expect(page.getByText("正在读取微任务…", { exact: true })).toBeVisible();
    await expect(back).toBeVisible();
    await back.click();
    await expect(page.locator('[data-slot=task-work]')).toHaveAttribute("data-mode", "timeline");
    release();
    await page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a3"]:visible').click();
    await expect(page.getByRole("heading", { name: "读取微任务失败" })).toBeVisible();
    await expect(back).toBeVisible();
    await page.screenshot({ path: `${shots}/read-failed-${theme}-${width}.png` });
    await back.click();
    await expect(page.locator('[data-slot=task-work]')).toHaveAttribute("data-mode", "timeline");
    await expect(page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a3"]:focus')).toHaveCount(1);
  });

});

for (const theme of ["light", "dark"]) test.describe(`shared scroll bars ${theme}`, () => {
  // Both axes overflow at this height, so the shared area's bars are deterministic.
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 400 }, reducedMotion: "reduce" });
  test("shows one vertical bar at the tracks' right edge on hover, and the names column never shows its own", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, `#tasks/${macro}`);
    await page.locator('[data-slot=zoom-ruler]:visible').press("+");
    const tracksRoot = page.locator('[data-slot=timeline-tracks]').locator("xpath=..");
    await page.mouse.move(5, 5);
    for (const bar of await tracksRoot.locator('[data-slot=scroll-area-scrollbar]').all()) await expect(bar).toHaveCSS("opacity", "0");
    await tracksRoot.hover();
    const bars = tracksRoot.locator('[data-slot=scroll-area-scrollbar]');
    await expect(bars).toHaveCount(2);
    for (const bar of await bars.all()) await expect(bar).toBeVisible();
    const boxes = await bars.evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect()));
    const vertical = boxes.find(box => box.height > box.width)!;
    const horizontal = boxes.find(box => box.width > box.height)!;
    const rootBox = (await tracksRoot.boundingBox())!;
    expect(vertical.width).toBeLessThan(vertical.height);
    expect(vertical.x + vertical.width).toBeCloseTo(rootBox.x + rootBox.width, 0);
    expect(horizontal.width).toBeGreaterThan(horizontal.height);
    const name = page.locator('[data-slot=timeline-row-name]').first();
    await name.hover();
    expect(await name.evaluate(node => Boolean(node.closest('[data-slot=scroll-area]')?.querySelector('[data-slot=timeline-tracks]')))).toBe(true);
    await expect(tracksRoot.locator('[data-slot=scroll-area-scrollbar][data-orientation=vertical]')).toHaveCount(1);
  });
});

for (const theme of ["light", "dark"]) test.describe(`model order ${theme}`, () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  test("gives sol and opus different colours and keeps them when a new model starts", async ({ page }) => {
    let appended = false;
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
      const response = await route.fetch(), data = await response.json();
      const source = data.spans.find((span: { kind: string }) => span.kind === "execution");
      const start = Date.parse(source.startAt);
      data.events = [];
      data.spans = (appended ? ["gpt-6.1-sol", "claude-opus-5-5", "new-model"] : ["gpt-6.1-sol", "claude-opus-5-5"]).map((model, index) => ({ ...source,
        spanId: `model-${index}`, runId: data.rows[0].runId, startAt: new Date(start + index * 120000).toISOString(), endAt: new Date(start + index * 120000 + 60000).toISOString(),
        buddy: { ...source.buddy, model }, state: "finished", uncertain: false, shutdownConfirmed: true, resultStatus: "ok", inquiries: [] }));
      await route.fulfill({ response, json: data });
    });
    await open(page, `#tasks/${macro}`);
    await page.locator('[data-slot=legend-trigger]').click();
    await expect(page.locator('[data-slot=timeline-legend]')).toContainText("Possibly stuck");
    const bars = page.locator('[data-slot=execution-bar]');
    await expect(bars).toHaveCount(2);
    const colors = await bars.evaluateAll(nodes => nodes.map(node => getComputedStyle(node).fill));
    expect(colors[0]).not.toBe(colors[1]);
    appended = true;
    await page.reload();
    await expect(bars).toHaveCount(3);
    const later = await bars.evaluateAll(nodes => nodes.map(node => getComputedStyle(node).fill));
    expect(later.slice(0, 2)).toEqual(colors);
    expect(new Set(later).size).toBe(3);
  });
});
