import { expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { contrastOf, expectNoSidewaysScroll, open, settle, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const run = "preview-run-a1";
const shots = fileURLToPath(new URL("../../../tmp/console-hints/steps", import.meta.url));
const pane = (page: Page) => page.locator('[data-slot=detail-pane]');
const markdown = "# 任务书的 Markdown\n\n先核对 `layout.tsx`，再检查 **布局**。\n\n## 验证清单\n\n" +
  Array.from({ length: 18 }, (_, index) => `- 第 ${index + 1} 项：读取完整的 Markdown 内容，验证任务书保持列表结构。`).join("\n");

async function expectHeaderRule(page: Page) {
  const result = await pane(page).evaluate(node => {
    const title = node.querySelector('[data-slot="pane-title"]')!;
    const tabs = node.querySelector('[role="tablist"]');
    const rule = tabs ?? node.querySelector('[data-slot="pane-divider"]')!;
    const content = node.querySelector('[data-slot="pane-content"]')!;
    const line = rule.getBoundingClientRect();
    const frame = node.getBoundingClientRect();
    const style = getComputedStyle(rule);
    const color = document.createElement("span");
    color.style.color = "var(--line2)"; node.append(color);
    const expectedColor = getComputedStyle(color).color; color.remove();
    const active = tabs?.querySelector('[data-state="active"]');
    const underline = active ? getComputedStyle(active, "::after") : null;
    const underlineBottom = active ? active.getBoundingClientRect().bottom - parseFloat(underline!.bottom) : null;
    const waits = [...node.querySelectorAll('[data-item-key^="span:queue:"], [data-item-key^="span:host:"], [data-item-key^="settle:"]')];
    return {
      width: line.width, paneWidth: frame.width, left: line.left, paneLeft: frame.left,
      thickness: style.borderBottomWidth, color: style.borderBottomColor, expectedColor,
      titleAbove: title.getBoundingClientRect().bottom <= line.bottom - 1,
      waitsAbove: waits.every(wait => wait.getBoundingClientRect().bottom <= line.bottom - 1),
      contentBelow: content.getBoundingClientRect().top >= line.bottom - 0.5,
      titleTop: getComputedStyle(title).paddingTop, titleBottom: getComputedStyle(title).paddingBottom,
      contentTop: getComputedStyle(content).paddingTop,
      underlineOverlaps: underlineBottom === null || underlineBottom >= line.bottom - 1 && underlineBottom - parseFloat(underline!.height) <= line.bottom,
      underlineVisible: underline === null || underline.opacity === "1",
    };
  });
  expect(result.thickness).toBe("1px");
  expect(result.color).toBe(result.expectedColor);
  expect(result.width).toBeCloseTo(result.paneWidth, 1);
  expect(result.left).toBeCloseTo(result.paneLeft, 1);
  expect(result.titleAbove && result.waitsAbove && result.contentBelow, JSON.stringify(result)).toBe(true);
  expect([result.titleTop, result.titleBottom, result.contentTop]).toEqual(["20px", "10px", "16px"]);
  expect(result.underlineOverlaps && result.underlineVisible, JSON.stringify(result)).toBe(true);
}

async function fixture(page: Page) {
  await page.route(`**/api/tasks/${run}`, async route => {
    const response = await route.fetch();
    const record = await response.json();
    record.task = markdown;
    record.acceptanceNote = "## Host 验收说明\n\n- 已检查 `layout.tsx`\n- **浏览器检查通过**";
    await route.fulfill({ response, json: record });
  });
  await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
    const response = await route.fetch();
    const timeline = await response.json();
    const ownEvents = timeline.events.filter((event: { runId: string }) => event.runId === run);
    const continuation = ownEvents.find((event: { kind: string }) => event.kind === "continue");
    continuation.requestId = "req-a1-1";
    const accepted = ownEvents.find((event: { kind: string }) => event.kind === "accept");
    const seq = Math.max(...timeline.events.map((event: { seq: number }) => event.seq));
    const at = Date.parse(accepted.at);
    timeline.events.push(
      { ...accepted, seq: seq + 1, kind: "takeover", at: new Date(at - 240_000).toISOString(), verdict: null, artifactId: null },
      { ...accepted, seq: seq + 2, kind: "cancel", at: new Date(at - 180_000).toISOString(), verdict: null, artifactId: null, note: "结束当前执行" },
    );
    await route.fulfill({ response, json: timeline });
  });
  await page.route("**/api/command", async route => {
    const request = route.request().postDataJSON();
    if (request.operation !== "workflow_get" || request.params.runId !== run) return route.continue();
    const response = await route.fetch();
    const data = await response.json();
    const history = data.result.audit;
    if (history) {
      const first = history.turns.find((turn: { turnIndex: number }) => turn.turnIndex === 1);
      first.disposition = "assistance";
      first.summary = "Worker 请求的摘要原文不应重复出现在回合结果里";
      const request = history.requests.find((request: { requestId: string }) => request.requestId === "req-a1-1");
      request.summary = "## Worker 请求\n\n需要核对 `sample/` 的边界。";
      request.attempted = "已读取 **工作树**。";
      request.neededWork = ["批准读取 `sample/`"];
      request.acceptance = "完成 **路径核对**。";
      request.expectedArtifacts = ["`boundary.md` 与检查结果"];
    }
    await route.fulfill({ response, json: data });
  });
}

for (const width of [1280, 390]) test.describe(`common step content dark zh ${width}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
    await fixture(page);
  });

  test("orders every step consistently and captures each step, including waits and inquiries", async ({ page }) => {
    await open(page, `#tasks/${macro}/${run}/1`);
    if (width === 1280) await expect(page.getByText("dsh-harness", { exact: true }).first()).toBeVisible();
    const steps = ["委派", "路由", "回合 1", "决定", "继续", "换手", "回合 2", "接管", "Host 停止", "验收"];
    const names = ["delegation", "routing", "turn-inquiries", "decision", "continue", "handoff", "turn-result", "takeover", "cancel", "acceptance"];
    await mkdir(shots, { recursive: true });
    for (let index = 0; index < steps.length; index++) {
      await page.evaluate(hash => { location.hash = hash; }, `#tasks/${macro}/${run}/${index + 1}`);
      const title = pane(page).locator('[data-slot=pane-title]');
      await expect(title.locator("h3")).toHaveText(steps[index]);
      const tabs = pane(page).getByRole("tablist");
      await expect(tabs).toHaveCount([1, 2, 6].includes(index) ? 1 : 0);
      const ordered = await pane(page).evaluate(node => {
        const title = node.querySelector('[data-slot=pane-title]')!;
        const tabs = node.querySelector('[role=tablist]');
        const viewport = node.querySelector('[data-slot=scroll-area-viewport]')!;
        const waits = [...node.querySelectorAll('[data-item-key^="span:queue:"], [data-item-key^="span:host:"], [data-item-key^="settle:"]')];
        const before = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
          && a.getBoundingClientRect().bottom <= b.getBoundingClientRect().top + 1;
        return before(title, tabs ?? viewport) && waits.every(wait => before(title, wait) && before(wait, tabs ?? viewport));
      });
      expect(ordered, steps[index]).toBe(true);
      await expectHeaderRule(page);
      await expectNoSidewaysScroll(page);
      if (index === 0) {
        await expect(pane(page)).not.toContainText("微任务 ID");
        await expect(pane(page)).not.toContainText("当前负责方");
      }
      if (index === 2) {
        await expect(pane(page)).toContainText("已向 Host 提交请求，见下一步。");
        await expect(pane(page)).not.toContainText("Worker 请求的摘要原文");
        await expect(pane(page)).not.toContainText("任务书的 Markdown");
        await expect(pane(page).locator('[data-slot=request-quote]').first()).toHaveCSS("border-left-width", "2px");
      }
      if (index === 5) await expect(pane(page).locator('[data-item-key^="span:queue:"]')).toContainText("排队");
      if (index === 9) {
        await expect(pane(page).getByRole("heading", { name: "Host 验收说明" })).toBeVisible();
        await expect(pane(page).locator("li code")).toHaveText("layout.tsx");
        await expect(pane(page).getByText("已验收", { exact: true })).toHaveCount(1);
        await expect(pane(page).getByText("SHA-256", { exact: true })).toHaveCount(0);
        await pane(page).getByRole("button", { name: /文件 1/ }).click();
        await expect(pane(page).getByText("SHA-256", { exact: true })).toBeVisible();
        await pane(page).getByRole("button", { name: /文件 1/ }).click();
      }
      await page.mouse.move(0, 0);
      await settle(page);
      if (width === 1280) await page.screenshot({ path: `${shots}/${names[index]}-zh-dark-1280.png` });
    }
  });

  test("keeps a turn's queue below its title and above its tabs, including the session tab", async ({ page }) => {
    // With the same buddy on both turns there is no handoff between the queue and turn 2.
    await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
      const response = await route.fetch();
      const timeline = await response.json();
      const turns = timeline.spans.filter((span: { runId: string; kind: string }) => span.runId === run && span.kind === "execution");
      turns[0].buddy = turns[1].buddy;
      await route.fulfill({ response, json: timeline });
    });
    await open(page, `#tasks/${macro}/${run}/6`);
    const title = pane(page).locator('[data-slot=pane-title]');
    await expect(title.locator("h3")).toHaveText("回合 2");
    const wait = pane(page).locator('[data-item-key^="span:queue:"]');
    const tabs = pane(page).getByRole("tablist");
    await expect(wait).toContainText("排队");
    expect((await title.boundingBox())!.y).toBeLessThan((await wait.boundingBox())!.y);
    expect((await wait.boundingBox())!.y).toBeLessThan((await tabs.boundingBox())!.y);
    await tabs.getByRole("tab", { name: "会话", exact: true }).click();
    await expect(title).toBeVisible();
    await expect(wait).toBeVisible();
    await expect(tabs).toHaveCSS("border-bottom-width", "1px");
    await expect(pane(page).getByRole("tabpanel")).toHaveCSS("padding-top", "16px");
    await tabs.getByRole("tab", { name: "概述", exact: true }).click();
    await expectHeaderRule(page);
    if (width === 1280) await page.screenshot({ path: `${shots}/turn-wait-zh-dark-1280.png` });
  });

  test("folds rendered height at eight lines without cutting Markdown and expands in place", async ({ page }) => {
    await open(page, `#tasks/${macro}/${run}/1`);
    if (width === 1280) await expect(page.getByText("dsh-harness", { exact: true }).first()).toBeVisible();
    const fold = pane(page).locator('[data-slot=fold]').first();
    const body = fold.locator('[data-slot=fold-body]');
    await expect(body).toHaveAttribute("data-folded", "true");
    await expect(body.locator("h1")).toHaveText("任务书的 Markdown");
    await expect(body.locator("li")).toHaveCount(18);
    await expect(body.locator("code")).toHaveText("layout.tsx");
    const geometry = await body.evaluate(node => ({ height: node.getBoundingClientRect().height, lines: parseFloat(getComputedStyle(node).lineHeight), full: node.scrollHeight, mask: getComputedStyle(node).maskImage }));
    expect(Math.abs(geometry.height / geometry.lines - 8)).toBeLessThan(0.1);
    expect(geometry.full).toBeGreaterThan(geometry.height);
    expect(geometry.mask).toContain("linear-gradient");
    const before = (await body.boundingBox())!;
    if (width === 1280) await page.screenshot({ path: `${shots}/markdown-folded-zh-dark-1280.png` });
    await fold.getByRole("button", { name: "展开", exact: true }).click();
    await expect(body).toHaveAttribute("data-folded", "false");
    const after = (await body.boundingBox())!;
    expect(after.x).toBe(before.x);
    expect(after.height).toBeGreaterThan(before.height);
    await expect(body.locator("li")).toHaveCount(18);
    if (width === 1280) await page.screenshot({ path: `${shots}/markdown-expanded-zh-dark-1280.png` });
    await fold.getByRole("button", { name: "收起", exact: true }).click();
    await expect(body).toHaveAttribute("data-folded", "true");
  });

  test("distinguishes a request from its response by a neutral rule and gray kind, and removes explanatory hovers", async ({ page }) => {
    await open(page, `#tasks/${macro}/${run}/5`);
    const quote = pane(page).locator('[data-slot=request-quote]');
    await expect(quote.locator("h2")).toHaveText("Worker 请求");
    await expect(quote).toHaveCSS("border-left-width", "2px");
    const kind = quote.locator('[data-slot=request-kind]');
    await expect(kind).toHaveText("协助请求");
    const colors = await kind.evaluate(node => ({ kind: getComputedStyle(node).color, label: getComputedStyle(node.closest("blockquote")!.querySelector("dt")!).color }));
    expect(colors.kind).toBe(colors.label);
    expect(await contrastOf(kind)).toBeGreaterThanOrEqual(4.5);
    const response = pane(page).locator('[data-slot=host-response]').first();
    await expect(response).toContainText("继续。读取合成样例目录已获批准");
    expect((await response.boundingBox())!.y).toBeGreaterThan((await quote.boundingBox())!.y);
    await expect(pane(page).locator("dt").filter({ hasText: /^(谁|决定|时刻)$/ })).toHaveCount(0);
    await kind.hover();
    await page.waitForTimeout(700);
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await page.locator('[data-slot=detail-strip]').getByRole("button", { name: "上一步", exact: true }).hover();
    await page.waitForTimeout(700);
    await expect(page.getByRole("tooltip")).toHaveCount(0);
  });
});
