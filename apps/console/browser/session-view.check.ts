import { expect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { open, test, expectNoSidewaysScroll } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const step = (run: string, number: number, macro = A) => `#tasks/${macro}/${run}/${number}`;
const session = (run: string, number: number, macro = A) => `${step(run, number, macro)}?tab=session`;
const long = session("preview-run-a1", 7);
const captures = fileURLToPath(new URL("../../../tmp/readability-r4/", import.meta.url));
async function openSession(page: Page, hash: string) {
  await open(page, hash);
  await expect(page.locator('[data-slot=session-view]')).toBeVisible();
}
const panel = (page: Page) => page.locator('[data-slot=session-view]');
const viewport = (page: Page) => panel(page).locator('[data-slot=scroll-area-viewport]');
const tabs = (page: Page) => page.locator('[data-slot=detail-tabs]');
const overviewTab = (page: Page, language: "en" | "zh" = "en") => tabs(page).getByRole("tab", { name: language === "zh" ? "概述" : "Overview" });
const sessionTab = (page: Page, language: "en" | "zh" = "en") => tabs(page).getByRole("tab", { name: language === "zh" ? "会话" : "Session" });
async function earlier(page: Page, name: string) {
  await viewport(page).evaluate(node => { node.scrollTop = 0; });
  await panel(page).getByRole("button", { name, exact: true }).click();
}

for (const language of ["en", "zh"] as const) for (const width of [1440, 390]) for (const theme of ["light", "dark"] as const) {
  test.describe(`session ${language} ${width} ${theme}`, () => {
    test.use({ locale: language === "zh" ? "zh-CN" : "en-US", viewport: { width, height: width === 390 ? 844 : 900 }, reducedMotion: "reduce", colorScheme: theme });
    test("shows full content, pages to the start, anchors prepends, and restores its position after refresh", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await openSession(page, long);
      const more = language === "zh" ? "加载更早内容" : "Load earlier content";
      await expect(panel(page)).toBeVisible();
      await expect(sessionTab(page, language)).toHaveAttribute("aria-selected", "true");
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expect(panel(page).getByText(language === "zh" ? "已结束" : "Ended", { exact: true })).toBeVisible();
      await expect(panel(page).getByText(/Codex/)).toContainText(language === "zh" ? "不提供" : "Not provided");
      await expect(panel(page)).not.toContainText(language === "zh" ? "返回" : "Back");
      await viewport(page).evaluate(node => { node.scrollTop = 0; });
      const first = panel(page).locator('[data-session-block]').first();
      const key = await first.getAttribute("data-session-block");
      const oldY = (await first.boundingBox())!.y;
      await earlier(page, more);
      await expect(panel(page).getByText(language === "zh" ? "已到开头" : "Start of session", { exact: true })).toBeAttached();
      const newY = await panel(page).locator('[data-session-block]').evaluateAll((nodes, key) => nodes.find(node => node.getAttribute("data-session-block") === key)!.getBoundingClientRect().y, key);
      expect(Math.abs(newY - oldY)).toBeLessThan(3);
      await viewport(page).evaluate(node => { node.scrollTop = node.scrollHeight; });
      const output = language === "zh" ? "输出正文" : "Output text";
      await expect(panel(page).getByRole("button", { name: language === "zh" ? "结果数据" : "Result data", exact: true })).toBeVisible();
      await panel(page).getByRole("button", { name: output, exact: true }).click();
      const terminal = panel(page).locator('[data-slot=session-terminal]');
      await expect(terminal).toContainText("FULL_OUTPUT_END");
      await expect(terminal).not.toContainText("开始输出");
      await expect(terminal).not.toContainText("增量 2：");
      await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
      await terminal.locator('xpath=ancestor::*[@data-slot="collapsible"][1]').getByRole("button", { name: language === "zh" ? "复制全文" : "Copy full text", exact: true }).click();
      expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("FULL_OUTPUT_END");
      expect((await terminal.innerText()).length).toBeGreaterThan(20_000);
      await expectNoSidewaysScroll(page);
      if (language === "en") {
        mkdirSync(captures, { recursive: true });
        await page.screenshot({ path: `${captures}/session-${width}-${theme}.png`, fullPage: true });
      }
      const finalCopy = panel(page).locator('[data-session-block]').last().getByRole("button", { name: language === "zh" ? "复制全文" : "Copy full text", exact: true });
      await expect(finalCopy).toHaveCSS("opacity", "0");
      await finalCopy.focus();
      await expect(finalCopy).toHaveCSS("opacity", "1");
      await viewport(page).evaluate(async node => { node.scrollTop = 500; await new Promise(resolve => requestAnimationFrame(resolve)); });
      const before = await viewport(page).evaluate(node => node.scrollTop);
      await overviewTab(page, language).click();
      await expect(page).toHaveURL(step("preview-run-a1", 7));
      await expect(panel(page)).toBeHidden();
      await expect(panel(page)).toHaveCount(1);
      await sessionTab(page, language).click();
      await expect(page).toHaveURL(long);
      await expect(panel(page)).toBeVisible();
      await expect(panel(page).getByText(language === "zh" ? "已到开头" : "Start of session", { exact: true })).toBeAttached();
      await expect(panel(page).getByRole("button", { name: output, exact: true })).toHaveAttribute("aria-expanded", "true");
      await expect.poll(() => viewport(page).evaluate(node => node.scrollTop)).toBeCloseTo(before, 0);
      await finalCopy.focus();
      await viewport(page).evaluate(node => new Promise<void>(resolve => {
        node.addEventListener("scroll", () => resolve(), { once: true });
        node.scrollTop = 500;
      }));
      const saved = await viewport(page).evaluate(node => node.scrollTop);
      await page.reload();
      await expect(panel(page).getByText(language === "zh" ? "已到开头" : "Start of session", { exact: true })).toBeAttached();
      await expect.poll(() => viewport(page).evaluate(node => node.scrollTop)).toBeCloseTo(saved, 0);
      await expect(finalCopy).toBeFocused();
      await page.goBack();
      await expect(page).toHaveURL(step("preview-run-a1", 7));
      await expect(panel(page)).toBeHidden();
      await page.goForward();
      await expect(page).toHaveURL(long);
      await expect(panel(page)).toBeVisible();
      await expect.poll(() => viewport(page).evaluate(node => node.scrollTop)).toBeCloseTo(saved, 0);
      await expect(finalCopy).toBeFocused();
    });
  });
}

test.describe("session navigation and requests", () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  test("keeps copy controls beside message text and fold titles on a narrow screen", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openSession(page, long);
    await earlier(page, "Load earlier content");
    for (const from of ["user", "assistant"]) {
      const message = panel(page).locator(`[data-slot=session-message][data-from=${from}]`).first();
      await expect(message).toBeVisible();
      const geometry = await message.evaluate(node => {
        const content = node.querySelector<HTMLElement>('[data-slot="session-message-content"]')!;
        const response = content.lastElementChild!.getBoundingClientRect();
        const copy = content.querySelector<HTMLButtonElement>('[data-session-focus$=":copy"]')!.getBoundingClientRect();
        return { textOffset: response.top - content.getBoundingClientRect().top, copyOffset: copy.top - response.top, radius: getComputedStyle(content).borderTopLeftRadius };
      });
      expect(geometry.textOffset).toBeLessThan(12);
      expect(Math.abs(geometry.copyOffset)).toBeLessThan(12);
      if (from === "user") expect(geometry.radius).toBe("0px");
    }
    const fold = panel(page).getByRole("button", { name: "Tool input", exact: true }).locator('xpath=ancestor::*[@data-slot="collapsible"][1]');
    await expect(fold).toBeVisible();
    const foldGeometry = await fold.evaluate(node => {
      const title = node.querySelector<HTMLElement>('[data-slot="collapsible-trigger"]')!.getBoundingClientRect();
      const copy = node.querySelector<HTMLButtonElement>('[data-session-focus$=":copy"]')!.getBoundingClientRect();
      return { top: Math.abs(copy.top - title.top), right: Math.abs(copy.right - title.right) };
    });
    expect(foldGeometry.top).toBeLessThan(4);
    expect(foldGeometry.right).toBeLessThan(4);
    await expectNoSidewaysScroll(page);
  });

  test("offers Session only on routing and execution steps", async ({ page }) => {
    await open(page, step("preview-run-a1", 1));
    await expect(tabs(page)).toHaveCount(0);
    await page.goto(step("preview-run-a1", 2));
    await expect(sessionTab(page)).toBeVisible();
    await expect(overviewTab(page)).toHaveAttribute("aria-selected", "true");
    await page.goto(step("preview-run-a1", 3));
    await expect(sessionTab(page)).toBeVisible();
    await page.goto(step("preview-run-a1", 4));
    await expect(tabs(page)).toHaveCount(0);
  });

  test("opens the selected turn through its detail Session tab", async ({ page }) => {
    const loaded: string[] = [];
    page.on("request", request => { if (request.url().endsWith(".js")) loaded.push(request.url()); });
    await open(page, `#tasks/${A}`);
    expect(loaded.some(url => /SessionPanel|highlight|shiki/.test(url))).toBe(false);
    const span = page.locator('[data-slot=timeline-tracks] [data-key="span:execution:att-a1-2"]');
    await span.click();
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(step("preview-run-a1", 7));
    await expect(sessionTab(page)).toBeVisible();
    await sessionTab(page).click();
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(long);
    await expect(panel(page)).toBeVisible();
    await expect(panel(page)).not.toContainText("Turn 2 · Session");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.goBack();
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(step("preview-run-a1", 7));
    await expect(panel(page)).toBeHidden();
    await page.goForward();
    await expect(panel(page)).toBeVisible();
    await page.reload();
    await expect(sessionTab(page)).toHaveAttribute("aria-selected", "true");
    await expect(panel(page)).toBeVisible();
    expect(loaded.some(url => /SessionPanel/.test(url))).toBe(true);
  });

  test("loads the highlighter and requested grammar only after a code disclosure opens", async ({ page }) => {
    const scripts: string[] = [];
    page.on("request", request => { if (request.url().endsWith(".js")) scripts.push(request.url()); });
    await openSession(page, long);
    await expect(panel(page).getByRole("button", { name: "Tool input", exact: true })).toBeAttached();
    expect(scripts.some(url => /\/(?:core|json|javascript|typescript)-/.test(url))).toBe(false);
    await panel(page).getByRole("button", { name: "Tool input", exact: true }).click();
    await expect.poll(() => scripts.some(url => /\/json-/.test(url))).toBe(true);
    await expect(panel(page).locator('[data-slot=session-code] pre:visible')).toContainText("synthetic-preview-check");
    await expect(panel(page).getByRole("alert")).toHaveCount(0);
  });

  test("shows Markdown code without a click and explains why recording stopped", async ({ page }) => {
    const scripts: string[] = [];
    page.on("request", request => { if (request.url().endsWith(".js")) scripts.push(request.url()); });
    await page.route('**/api/tasks/*/attempts/*/session?*', async route => {
      const response = await route.fetch();
      const body = await response.json();
      body.events = [{ sessionId: "att-a1-2", update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "```json\n{\"ready\":true}\n```" } } }];
      body.stopped = { code: "session-record-write-failed", params: { category: "write", message: "synthetic disk error" } };
      await route.fulfill({ response, json: body });
    });
    await openSession(page, long);
    await expect(panel(page).getByRole("status")).toContainText("Session recording stopped: The session record could not be written.");
    await expect(panel(page).locator('[data-slot=session-markdown-code]')).toBeVisible();
    await expect(panel(page).locator('[data-slot=session-code]')).toContainText('{"ready":true}');
    await expect.poll(() => scripts.some(url => /\/json-/.test(url))).toBe(true);
    await expect(panel(page).getByRole("button", { name: /Collapse code/ })).toHaveAttribute("aria-expanded", "true");
    await expect(panel(page).getByRole("heading", { name: "Response", includeHidden: true })).toHaveClass(/sr-only/);
  });

  test("keeps a reader away from the bottom until New content is pressed, then follows additions", async ({ page }) => {
    await page.clock.install();
    await openSession(page, session("preview-run-a3", 3));
    await expect(panel(page).getByText("Running", { exact: true })).toBeVisible();
    await viewport(page).evaluate(node => { node.scrollTop = 0; });
    await page.clock.fastForward(12_000);
    await expect(panel(page).getByRole("button", { name: "New content" })).toBeVisible();
    expect(await viewport(page).evaluate(node => node.scrollTop)).toBe(0);
    await panel(page).getByRole("button", { name: "New content" }).click();
    await page.clock.fastForward(6000);
    await expect.poll(() => viewport(page).evaluate(node => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThan(48);
  });

  test("follows tool output in the main viewport and preserves its reader's position when away", async ({ page }) => {
    await page.clock.install();
    let next = 0;
    await page.route('**/api/tasks/*/attempts/*/session?*', async route => {
      const after = new URL(route.request().url()).searchParams.get("after");
      const update = after === null
        ? { sessionUpdate: "tool_call", toolCallId: "stream-tool", title: "Synthetic streaming output", kind: "execute", status: "in_progress", content: [{ type: "content", content: { type: "text", text: "Initial output line\n".repeat(120) } }], _meta: { nativeItemId: "stream-tool", contentSegment: "stdout" } }
        : { sessionUpdate: "tool_call_update", toolCallId: "stream-tool", content: [{ type: "content", content: { type: "text", text: `stream-tail-${++next}\n` } }], _meta: { nativeItemId: "stream-tool", contentSegment: "stdout" } };
      const before = after === null ? 61 : Number(after);
      await route.fulfill({ json: { state: "running", stopped: null, omitted: [], before, after: before + 1, hasEarlier: false, events: [{ sessionId: "att-a1-2", update }] } });
    });
    await openSession(page, long);
    await panel(page).getByRole("button", { name: "Output text", exact: true }).click();
    await viewport(page).evaluate(node => { node.scrollTop = node.scrollHeight; });
    await page.clock.runFor(20);
    const first = page.waitForResponse(response => response.url().includes("/session?") && response.url().includes("after="));
    await page.clock.fastForward(3000);
    await first;
    await expect(panel(page).locator('[data-slot=session-terminal]')).toContainText("stream-tail-1");
    await expect.poll(() => viewport(page).evaluate(node => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThan(48);
    await viewport(page).evaluate(node => new Promise<void>(resolve => {
      node.addEventListener("scroll", () => resolve(), { once: true });
      node.scrollTop = 0;
    }));
    await page.clock.runFor(20);
    const second = page.waitForResponse(response => response.url().includes("/session?") && response.url().includes("after="));
    await page.clock.fastForward(3000);
    await second;
    await expect(panel(page).getByRole("button", { name: "New content" })).toBeVisible();
    expect(await viewport(page).evaluate(node => node.scrollTop)).toBe(0);
  });

  test("cancels a pending request when its tab is hidden or another step opens", async ({ page }) => {
    let pending = 0;
    await page.route('**/api/tasks/*/attempts/*/session?*', async route => { pending++; await new Promise<void>(resolve => setTimeout(resolve, 700)); await route.continue().catch(() => {}); });
    await openSession(page, long);
    await expect.poll(() => pending).toBe(1);
    const cancelled = page.waitForEvent("requestfailed", { predicate: request => request.url().includes("/session?") });
    await overviewTab(page).click();
    expect((await cancelled).failure()?.errorText).toContain("ABORTED");
    await sessionTab(page).click();
    await expect.poll(() => pending).toBe(2);
    const switched = page.waitForEvent("requestfailed", { predicate: request => request.url().includes("/session?") });
    await page.evaluate(hash => { location.hash = hash; }, session("preview-run-a1", 2));
    expect((await switched).failure()?.errorText).toContain("ABORTED");
    await expect(panel(page).getByText("为合成任务选择执行 buddy。", { exact: true })).toBeVisible();
  });

  for (const [code, message] of [
    ["SESSION_NOT_INTEGRATED", "Session view is not implemented for this harness."],
    ["SESSION_NOT_RECORDED", "Session was not recorded for this execution."],
    ["SESSION_DELETED", "This execution’s session was deleted."],
    ["SESSION_INVALID_RECORD", "The session record is damaged and cannot be displayed."],
    ["SESSION_UNREADABLE", "The session could not be read. Try again."],
  ]) test(`explains ${code}`, async ({ page }) => {
    // Expected error responses do not masquerade as an empty conversation.
    await page.route('**/api/tasks/*/attempts/*/session?*', async route => {
      await route.fulfill({ status: 200, json: { ok: false, error: { code, message: "synthetic session failure" } } });
    });
    await openSession(page, long);
    await expect(panel(page).getByRole("alert")).toContainText(message);
    await expect(panel(page).getByText("This execution has no session content yet")).toHaveCount(0);
  });
});
