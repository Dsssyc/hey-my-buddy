import { expect, type Page } from "@playwright/test";
import { open, screenshots, test, expectNoSidewaysScroll } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";
const D = "macro-33333333-3333-4333-8333-333333333333";
const session = (taskId: string, attemptId: string, macro = A, detail = "") => `#tasks/${macro}${detail}?${new URLSearchParams({ sessionTask: taskId, sessionAttempt: attemptId })}`;
const long = session("preview-run-a1", "att-a1-2");
async function openSession(page: Page, hash: string) {
  const response = await page.goto(hash);
  expect(response?.headers()["x-hey-my-buddy-preview"]).toBe("synthetic-fixture-data");
  await page.locator('[data-slot=session-view]').waitFor();
}
const dialog = (page: Page) => page.locator('[data-slot=session-view]');
const viewport = (page: Page) => dialog(page).locator('[data-slot=scroll-area-viewport]');
async function earlier(page: Page, name: string) {
  await viewport(page).evaluate(node => { node.scrollTop = 0; });
  await dialog(page).getByRole("button", { name, exact: true }).click();
}

for (const language of ["en", "zh"] as const) for (const width of [1440, 390]) for (const theme of ["light", "dark"] as const) {
  test.describe(`session ${language} ${width} ${theme}`, () => {
    test.use({ locale: language === "zh" ? "zh-CN" : "en-US", viewport: { width, height: width === 390 ? 844 : 900 }, reducedMotion: "reduce", colorScheme: theme });
    test("shows full content, pages to the start, anchors prepends, and restores its position after refresh", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await openSession(page, long);
      const back = language === "zh" ? "返回" : "Back";
      const more = language === "zh" ? "加载更早内容" : "Load earlier content";
      await expect(dialog(page)).toBeVisible();
      await expect(dialog(page).getByText(language === "zh" ? "已结束" : "Ended", { exact: true })).toBeVisible();
      await expect(dialog(page).getByText(/Codex/)).toContainText(language === "zh" ? "不提供" : "Not provided");
      await viewport(page).evaluate(node => { node.scrollTop = 0; });
      const first = dialog(page).locator('[data-session-block]').first();
      const key = await first.getAttribute("data-session-block");
      const oldY = (await first.boundingBox())!.y;
      await earlier(page, more);
      await expect(dialog(page).getByText(language === "zh" ? "已到开头" : "Start of session", { exact: true })).toBeAttached();
      const newY = await dialog(page).locator('[data-session-block]').evaluateAll((nodes, key) => nodes.find(node => node.getAttribute("data-session-block") === key)!.getBoundingClientRect().y, key);
      expect(Math.abs(newY - oldY)).toBeLessThan(3);
      await viewport(page).evaluate(node => { node.scrollTop = node.scrollHeight; });
      const output = language === "zh" ? "工具输出" : "Tool output";
      await dialog(page).getByRole("button", { name: output, exact: true }).click();
      const terminal = dialog(page).locator('[data-slot=session-terminal]');
      await expect(terminal).toContainText("FULL_OUTPUT_END");
      await expect(terminal).not.toContainText("开始输出");
      await expect(terminal).not.toContainText("增量 2：");
      await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
      await terminal.locator('xpath=ancestor::*[@data-slot="collapsible"][1]').getByRole("button", { name: language === "zh" ? "复制全文" : "Copy full text", exact: true }).click();
      expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("FULL_OUTPUT_END");
      expect((await terminal.innerText()).length).toBeGreaterThan(20_000);
      await expectNoSidewaysScroll(page);
      await page.screenshot({ path: `${screenshots}/session-${language}-${width}-${theme}.png`, fullPage: true });
      const finalCopy = dialog(page).locator('[data-session-block]').last().getByRole("button", { name: language === "zh" ? "复制全文" : "Copy full text", exact: true });
      await finalCopy.focus();
      await viewport(page).evaluate(async node => { node.scrollTop = 500; await new Promise(resolve => requestAnimationFrame(resolve)); });
      const before = await viewport(page).evaluate(node => node.scrollTop);
      await page.reload();
      await expect(dialog(page).getByText(language === "zh" ? "已到开头" : "Start of session", { exact: true })).toBeAttached();
      await expect.poll(() => viewport(page).evaluate(node => node.scrollTop)).toBeCloseTo(before, 0);
      await expect(finalCopy).toBeFocused();
      await dialog(page).getByRole("button", { name: back, exact: true }).click();
      await expect(dialog(page)).toHaveCount(0);
      await page.goBack();
      await expect(dialog(page)).toBeVisible();
      await expect.poll(() => viewport(page).evaluate(node => node.scrollTop)).toBeCloseTo(before, 0);
      await expect(finalCopy).toBeFocused();
    });
  });
}

test.describe("session navigation and requests", () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  test("opens the selected attempt from its turn and directly from Worker and Router timeline fragments", async ({ page }) => {
    const loaded: string[] = [];
    page.on("request", request => { if (request.url().endsWith(".js")) loaded.push(request.url()); });
    await open(page, `#tasks/${A}`);
    expect(loaded.some(url => /SessionDialog|highlight|shiki/.test(url))).toBe(false);
    const span = page.locator('[data-slot=timeline-tracks] [data-key="span:execution:att-a1-2"]');
    await span.dblclick();
    const button = page.locator('[data-slot=detail-pane]').getByRole("button", { name: "View session" });
    await button.click();
    await expect(page).toHaveURL(/sessionAttempt=att-a1-2/);
    await expect(dialog(page).getByRole("heading", { name: "Turn 2 · Session" })).toBeVisible();
    await dialog(page).getByRole("button", { name: "Back", exact: true }).click();
    await expect(button).toBeFocused();
    await page.goBack();
    await expect(dialog(page)).toBeVisible();
    await page.reload();
    await expect(dialog(page).getByRole("heading", { name: "Turn 2 · Session" })).toBeVisible();
    await dialog(page).getByRole("button", { name: "Back", exact: true }).click();
    await expect(button).toBeFocused();
    await page.goBack();
    await expect(dialog(page)).toBeVisible();
    await page.goBack();
    await expect(dialog(page)).toHaveCount(0);
    await page.goto(`#tasks/${A}`);
    await span.focus();
    const direct = page.locator('[data-slot=timeline-status]').getByRole("button", { name: "View session" });
    await direct.focus();
    await expect(direct).toBeFocused();
    await direct.press("Enter");
    await expect(dialog(page)).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`#tasks/${A}\\?`));
    await dialog(page).getByRole("button", { name: "Back", exact: true }).click();
    const router = page.locator('[data-slot=timeline-tracks] [data-key="span:routing:att-router-a1"]');
    await router.click();
    await page.locator('[data-slot=timeline-status]').getByRole("button", { name: "View session" }).click();
    await expect(page).toHaveURL(/sessionTask=preview-run-a1-router.*sessionAttempt=att-router-a1/);
    await expect(dialog(page).getByRole("heading", { name: "Route · Session" })).toBeVisible();
    expect(loaded.some(url => /SessionDialog/.test(url))).toBe(true);
  });

  test("loads the highlighter and requested grammar only after a code disclosure opens", async ({ page }) => {
    const scripts: string[] = [];
    page.on("request", request => { if (request.url().endsWith(".js")) scripts.push(request.url()); });
    await openSession(page, long);
    await expect(dialog(page).getByRole("button", { name: "Tool input", exact: true })).toBeAttached();
    expect(scripts.some(url => /\/(?:core|json|javascript|typescript)-/.test(url))).toBe(false);
    await dialog(page).getByRole("button", { name: "Tool input", exact: true }).click();
    await expect.poll(() => scripts.some(url => /\/json-/.test(url))).toBe(true);
    await expect(dialog(page).locator('[data-slot=session-code] pre:visible')).toContainText("synthetic-preview-check");
    await expect(dialog(page).getByRole("alert")).toHaveCount(0);
  });

  test("keeps a reader away from the bottom until New content is pressed, then follows additions", async ({ page }) => {
    await page.clock.install();
    await openSession(page, session("preview-run-a3", "att-a3-1"));
    await expect(dialog(page).getByText("Running", { exact: true })).toBeVisible();
    await viewport(page).evaluate(node => { node.scrollTop = 0; });
    await page.clock.fastForward(12_000);
    await expect(dialog(page).getByRole("button", { name: "New content" })).toBeVisible();
    expect(await viewport(page).evaluate(node => node.scrollTop)).toBe(0);
    await dialog(page).getByRole("button", { name: "New content" }).click();
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
      await route.fulfill({ json: { state: "running", omitted: [], before, after: before + 1, hasEarlier: false, events: [{ sessionId: "att-a1-2", update }] } });
    });
    await openSession(page, long);
    await dialog(page).getByRole("button", { name: "Tool output", exact: true }).click();
    await viewport(page).evaluate(node => { node.scrollTop = node.scrollHeight; });
    await page.clock.runFor(20);
    const first = page.waitForResponse(response => response.url().includes("/session?") && response.url().includes("after="));
    await page.clock.fastForward(3000);
    await first;
    await expect(dialog(page).locator('[data-slot=session-terminal]')).toContainText("stream-tail-1");
    await expect.poll(() => viewport(page).evaluate(node => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThan(48);
    await viewport(page).evaluate(node => { node.scrollTop = 0; });
    await page.clock.runFor(20);
    const second = page.waitForResponse(response => response.url().includes("/session?") && response.url().includes("after="));
    await page.clock.fastForward(3000);
    await second;
    await expect(dialog(page).getByRole("button", { name: "New content" })).toBeVisible();
    expect(await viewport(page).evaluate(node => node.scrollTop)).toBe(0);
  });

  test("cancels a pending request when closed or switched to another attempt", async ({ page }) => {
    let pending = 0;
    await page.route('**/api/tasks/*/attempts/*/session?*', async route => { pending++; await new Promise<void>(resolve => setTimeout(resolve, 700)); await route.continue().catch(() => {}); });
    await openSession(page, long);
    await expect.poll(() => pending).toBe(1);
    const cancelled = page.waitForEvent("requestfailed", { predicate: request => request.url().includes("/session?") });
    await dialog(page).getByRole("button", { name: "Back", exact: true }).click();
    expect((await cancelled).failure()?.errorText).toContain("ABORTED");
    await page.evaluate(hash => { location.hash = hash; }, long);
    await expect.poll(() => pending).toBe(2);
    const switched = page.waitForEvent("requestfailed", { predicate: request => request.url().includes("/session?") });
    await page.evaluate(hash => { location.hash = hash; }, session("preview-run-a1-router", "att-router-a1"));
    expect((await switched).failure()?.errorText).toContain("ABORTED");
    await expect(dialog(page).getByText("为合成任务选择执行 buddy。", { exact: true })).toBeVisible();
  });

  for (const [task, attempt, macro, text] of [
    ["preview-run-a2", "att-a2-1", A, "Session view is not implemented for this harness."],
    ["preview-run-b3", "att-b3-1", B, "Session was not recorded for this execution."],
    ["preview-run-d1", "att-d1-1", D, "This execution’s session was deleted."],
    ["preview-run-b7", "att-b7-1", B, "The session record is damaged and cannot be displayed."],
    ["preview-run-a1", "att-a1-1", A, "The session could not be read. Try again."],
  ]) test(`explains ${attempt}`, async ({ page }) => {
    // Expected error responses do not masquerade as an empty conversation.
    await page.route('**/api/tasks/*/attempts/*/session?*', async route => { const response = await route.fetch(); await route.fulfill({ status: 200, body: await response.text(), headers: { "content-type": "application/json" } }); });
    await openSession(page, session(task, attempt, macro));
    await expect(dialog(page).getByRole("alert")).toHaveText(new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    await expect(dialog(page).getByText("This execution has no session content yet")).toHaveCount(0);
  });
});
