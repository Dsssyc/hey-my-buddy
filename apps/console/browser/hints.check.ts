import { expect, type Locator, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, settle, test } from "./preview";
import { settingsFixture, storagePlan } from "./settings-fixtures";

const captures = fileURLToPath(new URL("../../../tmp/console-hints/pages", import.meta.url));
const macro = "macro-11111111-1111-4111-8111-111111111111";

async function quiet(page: Page, targets: Locator[]) {
  for (const target of targets) {
    await target.hover();
    await page.waitForTimeout(700);
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await expect(target).not.toHaveAttribute("title");
  }
}
async function capture(page: Page, name: string) {
  await page.mouse.move(0, 0);
  await settle(page);
  await mkdir(captures, { recursive: true });
  await page.screenshot({ path: `${captures}/${name}-zh-dark-1280.png` });
}
async function consequenceLine(target: Locator) {
  await expect(target).toBeVisible();
  const facts = await target.evaluate(node => {
    const range = document.createRange(); range.selectNodeContents(node);
    const rects = [...range.getClientRects()];
    const frame = node.getBoundingClientRect();
    const probe = document.createElement("span"); probe.style.color = "var(--ink3)"; node.append(probe);
    const expectedColor = getComputedStyle(probe).color; probe.remove();
    return { lines: new Set(rects.map(rect => rect.top)).size, color: getComputedStyle(node).color, expectedColor,
      fontSize: getComputedStyle(node).fontSize, whole: rects.every(rect => rect.left >= frame.left && rect.right <= frame.right + 1) };
  });
  expect(facts.lines).toBe(1);
  expect(facts.whole).toBe(true);
  expect(facts.fontSize).toBe("12px");
  expect(facts.color).toBe(facts.expectedColor);
}

test.describe("necessary hover hints and visible consequences", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1280, height: 1000 }, reducedMotion: "reduce" });
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
  });

  test("changes the mode consequence, keeps section names and arrows quiet and writes maintenance data flow below its setting", async ({ page }) => {
    await open(page, "#buddies/roles");
    const mode = page.getByRole("group", { name: "默认模式", exact: true });
    const consequence = mode.locator('[data-slot="routing-mode-consequence"]');
    const fast = mode.getByRole("radio", { name: "快速", exact: true });
    const review = mode.getByRole("radio", { name: "审阅", exact: true });
    await fast.click();
    await expect(consequence).toHaveText("需要 Router 判断时，任务的文字会发给 Router 的模型提供方");
    await consequenceLine(consequence);
    await capture(page, "router-fast");
    await review.click();
    await expect(consequence).toContainText("还会发送一份冻结的代码副本");
    await expect(consequence).toContainText("DSH 与 ZCode 没有系统沙盒");
    await consequenceLine(consequence);
    await capture(page, "router-review");
    const budget = page.getByRole("group", { name: "审阅预算", exact: true });
    const retry = page.getByRole("group", { name: "重试间隔", exact: true });
    const list = page.getByRole("list", { name: "Router 列表" });
    await quiet(page, [mode.getByText("默认模式", { exact: true }), fast, review,
      budget.getByText("审阅预算", { exact: true }), retry.getByText("重试间隔", { exact: true }),
      list.getByRole("button").nth(1), list.getByRole("button").nth(2)]);
    const maintenance = page.locator('[data-slot="maintenance-consequence"]');
    await maintenance.scrollIntoViewIfNeeded();
    await expect(maintenance).toBeVisible();
    await expect(maintenance).toContainText("维护");
    await consequenceLine(maintenance);
    expect((await maintenance.boundingBox())!.y).toBeGreaterThan((await page.getByRole("button", { name: "选择 buddy", exact: true }).boundingBox())!.y);
    await capture(page, "maintenance");
    const domainArrow = page.locator('[data-slot="domain-tree"] button[aria-expanded]').first();
    await quiet(page, [maintenance, domainArrow, page.getByRole("group", { name: "记录", exact: true }).getByText("失败", { exact: true })]);
  });

  test("keeps locked choices' reasons without appending mode explanations", async ({ page }) => {
    await page.route("**/api/command", async route => {
      if (route.request().postDataJSON().operation !== "user_policy_publish") return route.fallback();
      await route.fulfill({ json: { ok: false, error: { code: "CONSOLE_SESSION_EXPIRED", message: "synthetic expired session" } } });
    });
    await open(page, "#buddies/roles");
    await page.getByRole("radio", { name: "审阅", exact: true }).click();
    await page.getByRole("button", { name: "保存", exact: true }).click();
    for (const target of [page.getByRole("radio", { name: "快速", exact: true }),
      page.getByRole("radio", { name: "标准", exact: true }), page.getByRole("spinbutton", { name: "重试间隔秒数" })]) {
      await target.hover();
      await expect(page.getByRole("tooltip")).toHaveText("浏览器授权已失效，重新授权后可以修改");
      await page.mouse.move(0, 0);
    }
  });

  test("leaves model marks, folds and preferences quiet while keeping unavailable reasons", async ({ page }) => {
    await open(page, "#buddies/catalog");
    await quiet(page, [page.locator('[data-slot="fold-handle"]').first(),
      page.getByRole("button", { name: "Codex 账户", exact: true }),
      page.locator('[data-slot="model-row"] .rounded-full').first(),
      page.locator('[data-slot="model-row"]').getByText("待确认", { exact: true })]);
    await page.getByRole("button", { name: "codex / openai / gpt-5-codex", exact: true }).click();
    const drawer = page.locator('[data-slot="model-drawer"]');
    await quiet(page, [drawer.getByRole("radio", { name: "优先", exact: true }),
      drawer.getByRole("radio", { name: "固定", exact: true }), drawer.getByRole("radio", { name: "排除", exact: true })]);
    await page.getByRole("button", { name: "Claude Code 账户", exact: true }).click();
    const account = page.getByRole("group", { name: "Claude Code 设置", exact: true });
    const reason = account.locator('[aria-description]').filter({ hasText: "未登录" }).first();
    await reason.hover();
    await expect(page.getByRole("tooltip")).toContainText("登录");
  });

  test("writes storage check time and orphan consequences and keeps only protection and expiry reasons", async ({ page }) => {
    let expired = false;
    await settingsFixture(page);
    await page.route("**/api/command", async route => {
      if (route.request().postDataJSON().operation !== "storage_plan") return route.fallback();
      const plan = storagePlan();
      if (expired) plan.expiresAt = new Date(Date.now() - 1).toISOString();
      await route.fulfill({ json: { ok: true, result: { ...plan, orphanProcesses: [
        { pid: 4321, kind: "execution", stateDir: "/synthetic-preview/state", runtimeDir: "/synthetic-preview/runtime" },
      ] } } });
    });
    await open(page, "#settings");
    const input = page.getByRole("spinbutton", { name: "静默提醒阈值（分钟）" });
    const access = page.getByRole("region", { name: "访问", exact: true });
    await quiet(page, [input, access.locator("time").first()]);
    await input.fill("1441");
    const save = page.getByRole("button", { name: "保存", exact: true });
    await save.hover();
    await expect(page.getByRole("tooltip")).toContainText("1 至 1440");
    await input.fill("15");
    const storage = page.getByRole("region", { name: "存储", exact: true });
    const check = storage.getByRole("button", { name: "检查占用", exact: true });
    await check.click();
    const time = storage.locator('[data-slot="storage-check-time"]');
    await expect(time).toContainText("检查于");
    await expect(time).toContainText("内可清理");
    const orphans = storage.locator('[data-slot="storage-orphans"]');
    await orphans.scrollIntoViewIfNeeded();
    await expect(time).toBeInViewport();
    await expect(orphans.locator('[data-slot="orphans-consequence"]')).toHaveText("清理不会停止这些进程。");
    await consequenceLine(orphans.locator('[data-slot="orphans-consequence"]'));
    await capture(page, "storage");
    const ordinary = storage.getByRole("row").filter({ hasText: "保留的 Harness 目录" });
    await quiet(page, [check, time, storage.getByRole("button", { name: "回收…", exact: true }),
      ordinary.getByRole("cell").nth(1), ordinary.getByRole("cell").nth(2), orphans.getByRole("heading")]);
    const protectedRow = storage.getByRole("row").filter({ hasText: "备份" });
    await protectedRow.getByRole("cell").last().getByText("0 B").hover();
    await expect(page.getByRole("tooltip")).toContainText("黑板受保护");
    expired = true;
    await check.click();
    await storage.getByRole("button", { name: "回收…", exact: true }).hover();
    await expect(page.getByRole("tooltip")).toHaveText("计划已过期，请重新检查");
  });

  test("keeps timeline facts and name resizing quiet and retains the ruler's actions", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    const row = page.locator('[data-slot="timeline-row"][data-run-id="preview-run-a1"]');
    await quiet(page, [page.locator('[data-slot="timeline-name-divider"]'), row.locator('[data-key^="span:"]').first(),
      page.locator('[data-slot="event-cluster"]').first(), row.locator('[data-key^="event:"]').first()]);
    const readout = page.locator('[data-slot="timeline-readout"]');
    await expect(readout).not.toBeEmpty();
    await page.locator('[data-slot="zoom-ruler"]').first().focus();
    await page.keyboard.press("=");
    await page.getByRole("button", { name: "适应宽度", exact: true }).hover();
    await expect(page.getByRole("tooltip", { name: "适应宽度", exact: true })).toBeVisible();
    await expect(page.getByRole("tooltip", { name: /^滚轮缩放/ })).toBeVisible();
  });

  test("leaves session call status, kind and both copy affordances quiet", async ({ page }) => {
    await open(page, `#tasks/${macro}/preview-run-a1/7?tab=session`);
    const session = page.locator('[data-slot="session-view"]');
    const group = session.locator('[data-session-block="calls:tool:codex-setup-00"]');
    await group.locator('[data-slot="trace-collapsible-trigger"]').first().click();
    const tool = session.locator('[data-session-tool="tool:codex-setup-00"]');
    await tool.locator('[data-slot="trace-collapsible-trigger"]').click();
    await quiet(page, [tool.locator('[data-slot="session-call-status"]'),
      tool.locator('[data-slot="trace-icon"]'), tool.getByRole("button", { name: "复制全文", exact: true })]);
    const message = session.locator('[data-session-row="note"]').filter({ has: page.getByRole("button", { name: "复制全文", exact: true }) }).first();
    await quiet(page, [message.getByRole("button", { name: "复制全文", exact: true })]);
  });
});
