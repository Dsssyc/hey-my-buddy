import { expect, type Page } from "@playwright/test";
import { open, test } from "./preview";
import { fixtureCoverage } from "../src/session-view/test-fixtures";

const address = "#tasks/macro-11111111-1111-4111-8111-111111111111/preview-run-a1/7?tab=session";
const panel = (page: Page) => page.locator('[data-slot="session-view"]');
const viewport = (page: Page) => panel(page).locator('[data-slot="scroll-area-viewport"]');
const distance = (page: Page) => viewport(page).evaluate(node => node.scrollHeight - node.clientHeight - node.scrollTop);
const message = (text: string) => ({ sessionId: "att-a1-2", update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text } } });

for (const theme of ["light", "dark"] as const) test.describe(`session stick ${theme}`, () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 900 }, colorScheme: theme, reducedMotion: "reduce" });
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
  });

  // Both new message rows and a growing existing tool output must obey the same
  // follow / escape / return contract on the real Radix scrolling viewport.
  for (const format of ["messages", "tool"] as const) test(`${format}: follows additions, preserves upward reading, returns and resumes following`, async ({ page }) => {
    let next = 0;
    await page.route("**/api/tasks/*/attempts/*/session?*", async route => {
      const after = new URL(route.request().url()).searchParams.get("after");
      const initial = after === null;
      const text = initial ? "Initial output line\n".repeat(120) : `stream-tail-${++next}\n`;
      const events = format === "messages"
        ? initial ? Array.from({ length: 60 }, (_, index) => message(`Initial message ${index}`)) : [message(text)]
        : [{ sessionId: "att-a1-2", update: {
          sessionUpdate: initial ? "tool_call" : "tool_call_update", toolCallId: "stream-tool",
          ...(initial ? { title: "Synthetic streaming output", kind: "execute", status: "in_progress" } : {}),
          content: [{ type: "content", content: { type: "text", text } }],
          _meta: { nativeItemId: "stream-tool", contentSegment: "stdout" },
        } }];
      const before = initial ? 0 : Number(after);
      await route.fulfill({ json: { state: "running", stopped: null, coverage: fixtureCoverage, before, after: before + events.length, hasEarlier: false, events } });
    });
    await open(page, address);
    await expect(panel(page).locator('[data-session-block]').first()).toBeAttached();
    await viewport(page).evaluate(() => new Promise(requestAnimationFrame));
    if (format === "tool") {
      const call = panel(page).locator('[data-session-tool="tool:stream-tool"]');
      await call.locator('[data-slot="trace-collapsible-trigger"]').click();
      await call.getByRole("button", { name: /^Show remaining \d+ lines$/ }).click();
      await viewport(page).evaluate(() => new Promise(requestAnimationFrame));
      // Opening a disclosure preserves its interaction position. Scroll down as
      // a reader would to resume following the now fully visible output.
      await viewport(page).evaluate(node => new Promise<void>(resolve => {
        node.addEventListener("scroll", () => resolve(), { once: true });
        node.scrollTop = 100;
      }));
      await viewport(page).evaluate(() => new Promise(requestAnimationFrame));
      await viewport(page).evaluate(node => new Promise<void>(resolve => {
        node.addEventListener("scroll", () => resolve(), { once: true });
        node.scrollTop = node.scrollHeight;
      }));
      await viewport(page).evaluate(() => new Promise(requestAnimationFrame));
    }
    await expect.poll(() => distance(page)).toBeLessThanOrEqual(2);
    const poll = async (tail: number) => {
      const response = page.waitForResponse(value => value.url().includes("/session?") && value.url().includes("after="));
      await response;
      await expect(panel(page)).toContainText(`stream-tail-${tail}`);
      await viewport(page).evaluate(() => new Promise(requestAnimationFrame));
    };
    await poll(1);
    await expect.poll(() => distance(page)).toBeLessThanOrEqual(2);
    const more = panel(page).getByRole("button", { name: "New content", exact: true });
    await expect(more).toHaveCount(0);

    await viewport(page).hover();
    const scrolled = viewport(page).evaluate(node => new Promise<void>(resolve => node.addEventListener("scroll", () => resolve(), { once: true })));
    await page.mouse.wheel(0, -1400);
    await scrolled;
    await viewport(page).evaluate(() => new Promise(requestAnimationFrame));
    const reading = await viewport(page).evaluate(node => node.scrollTop);
    await poll(2);
    expect(await viewport(page).evaluate(node => node.scrollTop)).toBeCloseTo(reading, 0);
    await expect(more).toBeVisible();

    await more.click();
    // Immediate return also verifies reduced-motion: no intermediate smooth
    // scrolling after the first animation frame.
    await viewport(page).evaluate(() => new Promise(requestAnimationFrame));
    expect(await distance(page)).toBeLessThanOrEqual(2);
    await expect(more).toHaveCount(0);
    await expect(viewport(page)).toBeFocused();
    await poll(3);
    await expect.poll(() => distance(page)).toBeLessThanOrEqual(2);
    await expect(more).toHaveCount(0);
  });

  test("keeps following after a barely overflowing opening page reads earlier history automatically", async ({ page }) => {
    let earlierRead = false;
    let appended = false;
    await page.route("**/api/tasks/*/attempts/*/session?*", async route => {
      const params = new URL(route.request().url()).searchParams;
      const initial = !params.has("before") && !params.has("after");
      const earlier = params.has("before");
      if (earlier) earlierRead = true;
      const events = initial ? Array.from({ length: 10 }, (_, index) => message(`Opening record ${index}`))
        : earlier ? Array.from({ length: 50 }, (_, index) => message(`History record ${index}`))
          : appended ? [] : [message("Addition after earlier history")];
      if (!initial && !earlier) appended = true;
      await route.fulfill({ json: { state: "running", stopped: null, coverage: fixtureCoverage, before: initial ? 100 : earlier ? 0 : Number(params.get("after")), after: initial ? 110 : earlier ? 100 : 111, hasEarlier: initial, events } });
    });
    await open(page, address);
    await expect(panel(page).getByText("History record 0", { exact: true })).toBeAttached();
    expect(earlierRead).toBe(true);
    await expect.poll(() => distance(page)).toBeLessThanOrEqual(2);
    await expect(panel(page).getByText("Addition after earlier history", { exact: true })).toBeAttached();
    await expect.poll(() => distance(page)).toBeLessThanOrEqual(2);
    await expect(panel(page).getByRole("button", { name: "New content", exact: true })).toHaveCount(0);
  });

  for (const { top, mergeCalls } of [{ top: 0, mergeCalls: false }, { top: 180, mergeCalls: false }, { top: 0, mergeCalls: true }]) test(`keeps the visible record fixed when an earlier page arrives at scrollTop ${top}${mergeCalls ? " and merges call groups" : ""}`, async ({ page }) => {
    let earlierReads = 0;
    let deliver!: () => void;
    const ready = new Promise<void>(resolve => { deliver = resolve; });
    await page.route("**/api/tasks/*/attempts/*/session?*", async route => {
      const params = new URL(route.request().url()).searchParams;
      const initial = !params.has("before") && !params.has("after");
      const earlier = params.has("before");
      if (earlier) { earlierReads++; await ready; }
      const before = initial ? 100 : 0;
      const events: unknown[] = initial ? Array.from({ length: 60 }, (_, index) => message(`Latest record ${index}`))
        : earlier ? Array.from({ length: 50 }, (_, index) => message(`Earlier record ${index}`)) : [];
      const call = (id: string) => ({ sessionId: "att-a1-2", update: { sessionUpdate: "tool_call", toolCallId: id, title: id, kind: "execute", status: "completed" } });
      if (mergeCalls && initial) events.unshift(call("latest-call-1"), call("latest-call-2"));
      if (mergeCalls && earlier) events.push(call("earlier-call"));
      await route.fulfill({ json: { state: "ended", stopped: null, coverage: fixtureCoverage, before, after: initial ? 162 : earlier ? 100 : 162, hasEarlier: initial, events } });
    });
    try {
      await open(page, address);
      await expect(panel(page).getByText("Latest record 59", { exact: true })).toBeVisible();
      await expect.poll(() => distance(page)).toBeLessThanOrEqual(2);
      expect(earlierReads).toBe(0);
      const request = page.waitForRequest(value => value.url().includes("/session?") && value.url().includes("before=100"));
      await viewport(page).evaluate((node, offset) => { node.scrollTop = offset; }, top);
      await request;
      const anchor = await viewport(page).evaluate(node => {
        const frame = node.getBoundingClientRect();
        const record = [...node.querySelectorAll<HTMLElement>('[data-session-row="note"]')].find(item => item.getBoundingClientRect().top >= frame.top)!;
        return { key: record.dataset.sessionBlock!, top: record.getBoundingClientRect().top };
      });
      deliver();
      await expect(panel(page).getByText("Earlier record 0", { exact: true })).toBeAttached();
      if (mergeCalls) {
        await expect(panel(page).locator('[data-session-block="calls:tool:latest-call-1"]')).toHaveCount(0);
        await expect(panel(page).locator('[data-session-block="calls:tool:earlier-call"]')).toBeAttached();
      }
      const record = panel(page).locator(`[data-session-block='${anchor.key}']`);
      await expect.poll(async () => Math.abs((await record.boundingBox())!.y - anchor.top)).toBeLessThanOrEqual(1);
      await expect(panel(page).getByRole("button", { name: "New content", exact: true })).toHaveCount(0);
    } finally { deliver(); }
  });
});
