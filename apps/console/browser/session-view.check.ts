import { expect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { open, test, expectNoSidewaysScroll, screenshots } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";
const step = (run: string, number: number, macro = A) => `#tasks/${macro}/${run}/${number}`;
const session = (run: string, number: number, macro = A) => `${step(run, number, macro)}?tab=session`;
const long = session("preview-run-a1", 7);
const deep = session("preview-run-a1-h1", 3);
const correctionShots = fileURLToPath(new URL("../../../tmp", import.meta.url));
type Language = "en" | "zh";

const panel = (page: Page) => page.locator('[data-slot="session-view"]');
const viewport = (page: Page) => panel(page).locator('[data-slot="message-scroller-viewport"]');
const row = (page: Page, type: string) => panel(page).locator(`[data-session-row="${type}"]`);
const tool = (page: Page, id: string) => panel(page).locator(`[data-session-tool="tool:${id}"]`);
const notes = (page: Page) => panel(page).locator('[data-session-row="note"]');
const tabs = (page: Page) => page.locator('[data-slot="detail-tabs"]');
const overviewTab = (page: Page, language: Language = "en") => tabs(page).getByRole("tab", { name: language === "zh" ? "概述" : "Overview" });
const sessionTab = (page: Page, language: Language = "en") => tabs(page).getByRole("tab", { name: language === "zh" ? "会话" : "Session" });

async function openSession(page: Page, hash: string) {
  await open(page, hash);
  await expect(panel(page)).toBeVisible();
}
/** Scrolls to the top, which is what makes the panel read the next earlier page. */
async function reachTop(page: Page) {
  await viewport(page).evaluate(node => { node.scrollTop = 0; });
}
/** Waits until the reading of earlier content has finished applying. */
async function readOneEarlierPage(page: Page, before: number) {
  await reachTop(page);
  await expect.poll(() => notes(page).count(), { timeout: 15_000 }).toBeGreaterThan(before);
}
async function expandTool(page: Page, id: string) {
  const call = tool(page, id);
  await call.locator('[data-slot="trace-collapsible-trigger"]').click();
  await expect(call.locator('[data-slot="trace-collapsible-trigger"]')).toHaveAttribute("aria-expanded", "true");
  return call;
}

// All supported reading surfaces use the same row model. The expensive full
// output and history checks below run once against a representative surface.
for (const language of ["en", "zh"] as const) for (const width of [1440, 390]) for (const theme of ["light", "dark"] as const) {
  test.describe(`session ${language} ${width} ${theme}`, () => {
    test.use({ locale: language === "zh" ? "zh-CN" : "en-US", viewport: { width, height: width === 390 ? 844 : 900 }, reducedMotion: "reduce", colorScheme: theme });
    test("renders the current rows without horizontal overflow", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await openSession(page, long);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(sessionTab(page, language)).toHaveAttribute("aria-selected", "true");
      await expect(panel(page).getByText(language === "zh" ? "已结束" : "Ended", { exact: true })).toBeVisible();
      await expect(panel(page).getByText(/Codex/)).toContainText(language === "zh" ? "不提供" : "Not provided");
      await expect(row(page, "calls").first()).toBeAttached();
      await expect(tool(page, "tool-long")).toBeAttached();
      await expect(tool(page, "codex-failed-command")).toHaveCount(0);
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expectNoSidewaysScroll(page);
      if (language === "en" && width === 390 && theme === "light") {
        mkdirSync(screenshots, { recursive: true });
        await page.screenshot({ path: `${screenshots}/session-narrow-light.png`, fullPage: true });
      }
    });
  });
}

test.describe("session content and navigation", () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 900 }, reducedMotion: "reduce", colorScheme: "dark" });

  test("summarizes a turn's calls and failures, and reveals individual details", async ({ page }) => {
    await openSession(page, long);
    const longCall = await expandTool(page, "tool-long");
    await longCall.getByRole("button", { name: /^Show remaining \d+ lines$/ }).click();

    await expect(row(page, "calls").first()).toContainText(/Ran \d+ commands/);
    await expect(row(page, "marker").filter({ hasText: "Changes in this turn" })).toHaveCount(1);
    await panel(page).locator('[data-session-focus="calls:tool:codex-no-output-command"]').click();
    const failed = await expandTool(page, "codex-failed-command");
    await expect(failed.locator('[data-slot="trace-collapsible-trigger"]')).toContainText("synthetic-preview-missing --verify");
    await expect(failed).toContainText("command not found");
    await expect(failed.locator('[data-slot="session-result"]')).toHaveCount(0);
    await expect(failed.locator('[aria-label="Failed"]')).toBeAttached();

    const task = row(page, "task");
    await expect(task.locator('[data-slot="session-task"]')).toContainText("设计并实现工作目标时间轴");
    const prompt = task.getByRole("button", { name: /Expand full text sent to buddy/ });
    await expect(prompt).toHaveAttribute("aria-expanded", "false");
    await prompt.click();
    await expect(prompt).toHaveAttribute("aria-expanded", "true");
    await expect(task.locator('[data-slot="session-output"]')).toContainText("请检查合成预览的会话分页和完整工具输出。");
  });

  test("shows one output for duplicate Codex results and only non-null fields without a body", async ({ page }) => {
    await openSession(page, long);
    const completed = row(page, "calls").filter({ has: page.locator('[data-session-focus="calls:tool:codex-setup-00"]') });
    await expect(completed.locator('[data-slot="session-call-status"]')).toHaveAttribute("data-status", "completed");
    await completed.locator('[data-slot="trace-collapsible-trigger"]').first().click();
    const command = await expandTool(page, "codex-setup-00");
    await expect(command.locator('[data-slot="session-output"]')).toHaveCount(1);
    await expect(command.locator('[data-slot="session-result"]')).toHaveCount(0);
    await expect(command.locator('[data-slot="session-output"]')).toContainText("fixture-0: checked");

    const mixed = row(page, "calls").filter({ has: page.locator('[data-session-focus="calls:tool:codex-no-output-command"]') });
    await expect(mixed.locator('[data-slot="session-call-status"]')).toHaveAttribute("data-status", "failed");
    await mixed.locator('[data-slot="trace-collapsible-trigger"]').first().click();
    const noOutput = await expandTool(page, "codex-no-output-command");
    await expect(noOutput).toContainText("printf first");
    await expect(noOutput).toContainText("printf second");
    await expect(noOutput).not.toContainText("bash -lc");
    await expect(noOutput.locator('[data-slot="session-output"]')).toHaveCount(0);
    await expect(noOutput.locator('[data-slot="session-result"] dt')).toHaveText(["exitCode", "durationMs"]);
    await expect(noOutput.locator('[data-slot="session-result"] dd')).toHaveText(["0", "17"]);
    const edit = await expandTool(page, "codex-edit-fixture");
    await expect(edit.locator('[data-slot="session-output"]')).toHaveCount(1);
    await expect(edit.locator('[data-slot="session-result"]')).toHaveCount(0);
    await expect(edit.locator('[data-slot="session-output"]')).toContainText("+new");
  });

  test("shows active status on a running call group", async ({ page }) => {
    await openSession(page, session("preview-run-a3", 3));
    const group = row(page, "calls").filter({ has: page.locator('[data-session-focus="calls:tool:claude-running-read"]') });
    await expect(group.locator('[data-slot="session-call-status"]')).toHaveAttribute("data-status", "in_progress");
  });

  test("clamps a short page to the available scroll end when earlier content arrives", async ({ page }) => {
    const message = (text: string) => ({ sessionId: "att-a1-2", update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text } } });
    await page.route('**/api/tasks/*/attempts/*/session?*', async route => {
      const params = new URL(route.request().url()).searchParams;
      const opening = params.get("before") === null && params.get("after") === null;
      const earlier = params.get("before") !== null;
      // A session short enough to fit the viewport: the sentinel stays in view, so
      // the earlier page arrives without any scrolling. Later polls add nothing.
      const events = opening ? [message("短会话最新一条。"), message("短会话倒数第二条。")]
        : earlier ? [message("更早第三条。"), message("更早第二条。"), message("更早第一条。")] : [];
      await route.fulfill({ json: { state: "ended", stopped: null, omitted: [], before: opening ? 60 : 0, after: opening ? 62 : 60, hasEarlier: opening, events } });
    });
    await openSession(page, long);
    await expect(panel(page).getByText("更早第一条。")).toBeAttached();
    const { top, end } = await viewport(page).evaluate(node => ({ top: node.scrollTop, end: node.scrollHeight - node.clientHeight }));
    expect(top).toBeCloseTo(end, 0);
  });

  test("shows the first 14 output lines, then the full replacement snapshot", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
    await openSession(page, long);
    const call = await expandTool(page, "tool-long");
    const output = call.locator('[data-slot="session-output"]').first();
    const background = await output.locator('[data-slot="code-block"]').evaluate(node => {
      const sample = document.createElement("div");
      sample.style.backgroundColor = "var(--sunk)";
      document.body.appendChild(sample);
      const expected = getComputedStyle(sample).backgroundColor;
      sample.remove();
      return { actual: getComputedStyle(node).backgroundColor, expected };
    });
    expect(background.actual).toBe(background.expected);
    await expect(output).toContainText("最终完整快照");
    await expect(output).not.toContainText("FULL_OUTPUT_END");
    await expect(output).not.toContainText("开始输出");
    await expect(output).not.toContainText("增量 2：");
    const more = output.getByRole("button", { name: /^Show remaining \d+ lines$/ });
    await expect(more).toHaveAttribute("aria-expanded", "false");
    await more.click();
    await expect(output).toContainText("FULL_OUTPUT_END");
    await expect(output.locator('[data-slot="code-block"]')).toContainText("FULL_OUTPUT_END");
    expect((await output.innerText()).length).toBeGreaterThan(20_000);
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    await output.getByRole("button", { name: "Copy full text" }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("FULL_OUTPUT_END");
  });

  test("shows the native Codex turn diff as highlighted code and keeps command inputs concise", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
    await openSession(page, long);
    const command = await expandTool(page, "tool-long");
    await expect(command.locator("dt")).toHaveCount(0);
    await expect(command).not.toContainText("commandActions");
    await expect(command).not.toContainText("/synthetic-preview/hey-my-buddy");
    await expect(command.locator('[data-slot="session-output"]').first()).toContainText("最终完整快照");

    const marker = row(page, "marker").filter({ hasText: "Changes in this turn" });
    await expect(marker).toHaveCount(1);
    const trigger = marker.getByRole("button", { name: "Changes in this turn" });
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    await trigger.click();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    const diff = marker.locator('[data-slot="code-block"]');
    await expect(diff).toContainText("diff --git a/synthetic-preview/fixture.txt b/synthetic-preview/fixture.txt");
    await expect(diff).toContainText("-old fixture line");
    await expect(diff).toContainText("+new fixture line");
    await expect(diff).not.toContainText("[turn/diff/updated]");
    await expect.poll(() => diff.locator("span[style*='--shiki']").count()).toBeGreaterThan(0);
    const colors = await diff.evaluate(node => {
      const lines = [...node.querySelectorAll('[data-slot="code-block-line"]')];
      return ["-old fixture line", "+new fixture line"].map(text => {
        const line = lines.find(item => item.textContent?.includes(text));
        return line ? getComputedStyle(line.querySelector('[data-slot="code-block-line-content"] span[style]')!).color : null;
      });
    });
    expect(colors[0]).not.toBeNull();
    expect(colors[1]).not.toBeNull();
    expect(colors[0]).not.toBe(colors[1]);
    await marker.scrollIntoViewIfNeeded();
    mkdirSync(screenshots, { recursive: true });
    await page.screenshot({ path: `${screenshots}/session-codex-diff-dark.png`, fullPage: true });
  });

  test("starts from the latest page again after the session tab is left and reopened", async ({ page }) => {
    await openSession(page, deep);
    await expect.poll(() => notes(page).count(), { timeout: 15_000 }).toBeGreaterThan(0);
    const firstPage = await notes(page).count();
    await expect(row(page, "task")).toHaveCount(0);
    await readOneEarlierPage(page, firstPage);
    await readOneEarlierPage(page, await notes(page).count());
    expect(await notes(page).count()).toBeGreaterThan(firstPage);
    await overviewTab(page).click();
    await expect(panel(page)).toBeHidden();
    const fresh = page.waitForResponse(response => /\/session\?limit=256$/.test(response.url()));
    await sessionTab(page).click();
    await fresh;
    // Back exactly as a first open: only the latest page, read from its end.
    await expect.poll(() => notes(page).count()).toBe(firstPage);
    await expect(row(page, "task")).toHaveCount(0);
    await expect.poll(() => viewport(page).evaluate(node => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThan(48);
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
    await page.locator('[data-slot=timeline-tracks] [data-key="span:execution:att-a1-2"]').click();
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(step("preview-run-a1", 7));
    await sessionTab(page).click();
    await expect(page).toHaveURL(long);
    await expect(panel(page)).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.goBack();
    await expect(panel(page)).toBeHidden();
    await page.goForward();
    await expect(panel(page)).toBeVisible();
    await page.reload();
    await expect(sessionTab(page)).toHaveAttribute("aria-selected", "true");
    expect(loaded.some(url => /SessionPanel/.test(url))).toBe(true);
  });

  test("shows Markdown code and explains why recording stopped", async ({ page }) => {
    await page.route('**/api/tasks/*/attempts/*/session?*', async route => {
      const response = await route.fetch();
      const body = await response.json();
      body.events = [{ sessionId: "att-a1-2", update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "```json\n{\"ready\":true}\n```" } } }];
      body.stopped = { code: "session-record-write-failed", params: { category: "write", message: "synthetic disk error" } };
      await route.fulfill({ response, json: body });
    });
    await openSession(page, long);
    await expect(panel(page).getByRole("status")).toContainText("Session recording stopped: The session record could not be written.");
    await expect(row(page, "answer").locator('[data-slot="code-block"]')).toContainText('{"ready":true}');
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
    const call = await expandTool(page, "stream-tool");
    await call.getByRole("button", { name: /^Show remaining \d+ lines$/ }).click();
    await viewport(page).evaluate(node => new Promise<void>(resolve => {
      node.addEventListener("scroll", () => resolve(), { once: true });
      node.scrollTop = node.scrollHeight;
    }));
    await page.clock.runFor(20);
    const first = page.waitForResponse(response => response.url().includes("/session?") && response.url().includes("after="));
    await page.clock.fastForward(3000);
    await first;
    await expect(call.locator('[data-slot="session-output"]')).toContainText("stream-tail-1");
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
    const prompt = row(page, "task").getByRole("button", { name: /Expand full text sent to buddy/ });
    await prompt.click();
    await expect(row(page, "task")).toContainText("为合成任务选择执行 buddy。");
  });

  for (const [code, message] of [
    ["SESSION_NOT_INTEGRATED", "Session view is not implemented for this harness."],
    ["SESSION_NOT_RECORDED", "Session was not recorded for this execution."],
    ["SESSION_DELETED", "This execution’s session was deleted."],
    ["SESSION_INVALID_RECORD", "The session record is damaged and cannot be displayed."],
    ["SESSION_UNREADABLE", "The session could not be read. Try again."],
  ]) test(`explains ${code}`, async ({ page }) => {
    await page.route('**/api/tasks/*/attempts/*/session?*', async route => {
      await route.fulfill({ status: 200, json: { ok: false, error: { code, message: "synthetic session failure" } } });
    });
    await openSession(page, long);
    await expect(panel(page).getByRole("alert")).toContainText(message);
    await expect(panel(page).getByText("This execution has no session content yet")).toHaveCount(0);
  });
});

for (const theme of ["dark", "light"] as const) test.describe(`a session of many pages ${theme}`, () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 900 }, reducedMotion: "reduce", colorScheme: theme });

  test("reads every earlier page by scrolling to the top and stops at the beginning", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    let reads = 0;
    page.on("response", response => { if (response.url().includes("/session?") && response.url().includes("before=")) reads++; });
    // A short delay keeps the first earlier page in flight long enough to check
    // that its insertion leaves the newest content where the reader is.
    await page.route('**/api/tasks/*/attempts/*/session?*', async route => {
      if (route.request().url().includes("before=")) await page.waitForTimeout(300);
      await route.continue().catch(() => {});
    });
    await openSession(page, deep);
    await expect.poll(() => notes(page).count(), { timeout: 15_000 }).toBeGreaterThan(0);
    const firstPage = await notes(page).count();
    // Only the latest page is loaded; the opening still lies behind earlier pages.
    await expect(row(page, "task")).toHaveCount(0);
    const newest = panel(page).getByText("长会话结束：已核对到最新一条。");
    await reachTop(page);
    const keptY = (await newest.boundingBox())!.y;
    await expect.poll(() => notes(page).count(), { timeout: 15_000 }).toBeGreaterThan(firstPage);
    expect(Math.abs((await newest.boundingBox())!.y - keptY)).toBeLessThan(3);

    for (let guard = 0; guard < 30 && (await row(page, "task").count()) === 0; guard++) {
      const settled = await notes(page).count();
      await reachTop(page);
      await expect.poll(() => notes(page).count(), { timeout: 15_000 }).toBeGreaterThan(settled);
    }
    // The task row only renders once the opening event's page has been read.
    await expect(row(page, "task")).toBeAttached();
    expect(reads).toBe(12);
    // The beginning has been reached: scrolling to the top reads nothing more.
    await reachTop(page);
    await page.waitForTimeout(400);
    expect(reads).toBe(12);
  });
});

test.describe("the quiet reading line", () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 900 }, reducedMotion: "reduce", colorScheme: "dark" });

  test("says once that earlier content is being read, then leaves", async ({ page }) => {
    await page.route('**/api/tasks/*/attempts/*/session?*', async route => {
      if (route.request().url().includes("before=")) await page.waitForTimeout(400);
      await route.continue().catch(() => {});
    });
    await openSession(page, deep);
    await expect(panel(page).getByText("长会话结束：已核对到最新一条。")).toBeAttached();
    const loaded = await notes(page).count();
    await reachTop(page);
    const line = panel(page).locator('[data-slot="session-loading-earlier"]');
    await expect(line).toBeVisible();
    await expect(line).toContainText("Reading earlier content…");
    await expect(line).toHaveCount(0);
    await expect.poll(() => notes(page).count()).toBeGreaterThan(loaded);
  });
});

test.describe("four harness preview records", () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 900 }, reducedMotion: "reduce", colorScheme: "dark" });

  test("Claude Code keeps its interim note and presents StructuredOutput as the answer", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
    await openSession(page, session("preview-run-b10", 2, B));
    const group = row(page, "calls");
    await expect(group).toContainText("Read 1 file");
    await expect(group).toContainText("Searched 1 time");
    await group.locator('[data-slot="trace-collapsible-trigger"]').first().click();
    const read = await expandTool(page, "claude-read-guide");
    await expect(read).toContainText("file_path");
    await expect(read).toContainText("docs/reference/path-guide.md");
    await expect(read.locator('[data-slot="session-output"]')).toHaveCount(1);
    await expect(read.locator('[data-slot="session-result"]')).toHaveCount(0);
    await expect(read.locator('[data-slot="session-output"]')).toContainText("文档中的相对路径示例。");
    const search = await expandTool(page, "claude-search-path");
    await expect(search).toContainText("pattern");
    await expect(search).toContainText("synthetic/path");
    await expect(search).toContainText("docs/**/*.md");
    await expect(search.locator('[data-slot="trace-collapsible-trigger"]')).toContainText('“synthetic/path” docs/**/*.md');
    await expect(search.locator('[data-slot="session-output"]')).toHaveCount(1);
    await expect(search.locator('[data-slot="session-result"]')).toHaveCount(0);
    const note = row(page, "note");
    await expect(note).toContainText("路径示例与说明一致，准备提交结构化核对结果。");
    await expect(note).not.toContainText("Answer");
    const answer = row(page, "answer");
    await expect(answer).toHaveCount(1);
    await expect(answer).toContainText("独立核对完成：路径示例与说明一致。");
    await expect(answer.locator('[data-slot="session-answer-fields"] dt')).toHaveText(["disposition", "summary", "artifacts", "decisions", "remaining", "request"]);
    await expect(answer).not.toContainText('"outcome"');
    await expect(tool(page, "claude-structured-output")).toHaveCount(0);
    await answer.scrollIntoViewIfNeeded();
    mkdirSync(screenshots, { recursive: true });
    await page.screenshot({ path: `${screenshots}/session-claude-outcome-dark.png`, fullPage: true });
  });

  test("ZCode current input and blackboard actions remain distinct", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
    await openSession(page, session("preview-run-b1", 5, B));
    const read = await expandTool(page, "zcode-read");
    await expect(read).toContainText("synthetic/path-guide.txt");
    await expect(read).toContainText("path");
    await expect(read).toContainText("路径示例已读取。");
    await expect(read.locator('[data-slot="session-output"]')).toHaveCount(1);
    await expect(read.locator('[data-slot="session-result"]')).toHaveCount(0);
    await expect(row(page, "blackboard")).toHaveCount(2);
    await expect(row(page, "blackboard").first()).toContainText("Check Host questions");
    await expect(row(page, "blackboard").last()).toContainText("Return result");
    await expect(row(page, "answer")).toContainText("路径编码一致");
    mkdirSync(screenshots, { recursive: true });
    await page.screenshot({ path: `${screenshots}/session-zcode-dark.png`, fullPage: true });
  });

  test("ZCode old record shows its result without inventing missing input", async ({ page }) => {
    await openSession(page, session("preview-run-b1", 2, B));
    const read = await expandTool(page, "zcode-old-read");
    await expect(read).toContainText("旧记录没有保留工具输入。");
    await expect(read.locator('[data-slot="session-input"]')).toHaveCount(0);
    await expect(read.locator('[data-slot="session-result"] dt')).toHaveText(["content"]);
    await expect(row(page, "blackboard")).toHaveCount(0);
  });

  test("DSH renders a question and answer without tool rows", async ({ page }) => {
    await openSession(page, session("preview-run-b9", 5, B));
    await expect(row(page, "task")).toContainText("核对合成平台路径中的字符编码并交回结果");
    await row(page, "task").getByRole("button", { name: /Expand full text sent to buddy/ }).click();
    await expect(row(page, "task").locator('[data-slot="session-output"]')).toContainText("合成平台路径的编码是否一致？");
    await expect(row(page, "answer")).toContainText("合成样例里的路径可以正常读取");
    await expect(row(page, "calls")).toHaveCount(0);
    await expect(row(page, "tool")).toHaveCount(0);
  });

  test("DSH Router shows the routing task, full prompt and ordered decision fields", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
    await openSession(page, session("preview-run-a1", 2));
    const task = row(page, "task");
    await expect(task.locator("h3")).toHaveText("Choose a buddy for this micro task");
    await task.getByRole("button", { name: /Expand full text sent to buddy/ }).click();
    await expect(task.locator('[data-slot="session-output"]')).toContainText("ROLE: Router buddy");
    await expect(task.locator('[data-slot="session-output"]')).toContainText("为合成任务选择执行 buddy。");
    const answer = row(page, "answer");
    await expect(answer.locator('[data-slot="session-answer-fields"] dt')).toHaveText(["Selected buddy", "Explanation", "Evidence"]);
    await expect(answer).toContainText("dsh:deepseek-official:deepseek-flash:max");
    await expect(answer).toContainText("合成任务由 DeepSeek Flash 执行。");
    await expect(answer).toContainText("符合本次合成路由约束。");
    await expect(answer).not.toContainText('"buddyId"');
    mkdirSync(screenshots, { recursive: true });
    await page.screenshot({ path: `${screenshots}/session-router-dark.png`, fullPage: true });
  });

  test("review-mode Router abstention keeps its own task heading and structured decision", async ({ page }) => {
    await openSession(page, session("preview-run-a2", 5));
    await expect(row(page, "task").locator("h3")).toHaveText("Choose a buddy for this micro task (review mode)");
    await expect(row(page, "answer").locator('[data-slot="session-answer-fields"] dt')).toHaveText(["Selected buddy", "Explanation", "Evidence"]);
    await expect(row(page, "answer")).toContainText("null");
    await expect(row(page, "answer")).toContainText("合成证据不足，请 Host 选择。");
  });
});

for (const language of ["en", "zh"] as const) for (const theme of ["dark", "light"] as const) {
  test.describe(`named session markers ${language} ${theme}`, () => {
    test.use({ locale: language === "zh" ? "zh-CN" : "en-US", viewport: { width: 1440, height: 900 }, reducedMotion: "reduce", colorScheme: theme });
    test("counts each type and names it inside the group", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await openSession(page, long);
      const completed = panel(page).locator('[data-session-focus="calls:tool:codex-setup-00"] [data-slot="session-call-status"]');
      await expect(completed.locator(".lucide-check")).toHaveCount(1);
      const group = row(page, "calls").filter({ has: page.locator('[data-session-focus="calls:tool:codex-no-output-command"]') });
      const summary = group.locator('[data-slot="trace-collapsible-trigger"]').first();
      const text = await summary.innerText();
      if (language === "zh") {
        expect(text).toContain("子代理活动 2 次");
        expect(text).toContain("上下文压缩 1 次");
      } else {
        expect(text).toMatch(/sub.?agent activity.*2/i);
        expect(text).toMatch(/context compaction.*1/i);
      }
      await expect(group.locator('[data-slot="session-call-status"]')).toHaveAttribute("data-status", "failed");
      await expect(summary.locator(".lucide-x")).toHaveCount(1);
      await summary.click();
      const names = await group.locator('[data-slot="session-call-group"]').innerText();
      if (language === "zh") {
        expect(names.match(/子代理活动/g)).toHaveLength(2);
        expect(names.match(/上下文压缩/g)).toHaveLength(1);
      } else {
        expect(names.match(/sub.?agent activity/gi)).toHaveLength(2);
        expect(names.match(/context compaction/gi)).toHaveLength(1);
      }
      await expectNoSidewaysScroll(page);
      if (language === "en") {
        await expandTool(page, "codex-no-output-command");
        await expandTool(page, "codex-edit-fixture");
        await group.scrollIntoViewIfNeeded();
        mkdirSync(correctionShots, { recursive: true });
        await page.screenshot({ path: `${correctionShots}/session-view-corrections-${theme}.png`, fullPage: true });
      }
      await page.evaluate(hash => { location.hash = hash; }, session("preview-run-b10", 2, B));
      await panel(page).locator('[data-session-focus="calls:tool:claude-read-guide"]').click();
      await expect(tool(page, "claude-search-path").locator('[data-slot="trace-collapsible-trigger"]'))
        .toContainText("“synthetic/path” docs/**/*.md");
      await page.emulateMedia({ reducedMotion: "no-preference" });
      await page.evaluate(hash => { location.hash = hash; }, session("preview-run-a3", 3));
      const running = panel(page).locator('[data-session-focus="calls:tool:claude-running-read"] [data-slot="session-call-status"]');
      await expect(running).toHaveAttribute("data-status", "in_progress");
      await expect(running.locator(".lucide-loader-circle")).toHaveCSS("animation-name", "spin");
    });
  });
}

for (const language of ["en", "zh"] as const) test.describe(`structured answers on a narrow light screen: ${language}`, () => {
  test.use({ locale: language === "zh" ? "zh-CN" : "en-US", viewport: { width: 390, height: 844 }, reducedMotion: "reduce", colorScheme: "light" });
  test("keeps the Claude outcome and Router fields readable", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "light"));
    await openSession(page, session("preview-run-b10", 2, B));
    const claude = row(page, "answer");
    await expect(claude.locator('[data-slot="session-answer-fields"] dt')).toHaveText(["disposition", "summary", "artifacts", "decisions", "remaining", "request"]);
    await expectNoSidewaysScroll(page);
    await claude.scrollIntoViewIfNeeded();
    mkdirSync(screenshots, { recursive: true });
    await page.screenshot({ path: `${screenshots}/session-claude-outcome-${language}-narrow-light.png`, fullPage: true });

    await page.evaluate(hash => { location.hash = hash; }, session("preview-run-a1", 2));
    await expect(panel(page)).toBeVisible();
    const router = row(page, "answer");
    await expect(router.locator('[data-slot="session-answer-fields"] dt')).toHaveText(language === "zh" ? ["选中的 buddy", "说明", "依据"] : ["Selected buddy", "Explanation", "Evidence"]);
    await expectNoSidewaysScroll(page);
    await router.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${screenshots}/session-router-${language}-narrow-light.png`, fullPage: true });
  });
});
