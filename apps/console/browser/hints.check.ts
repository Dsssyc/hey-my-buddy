import { expect, type Locator, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expectNoSidewaysScroll, open, settle, test } from "./preview";
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
async function consequenceLine(target: Locator, singleLine = true, colorToken = "--ink3") {
  await expect(target).toBeVisible();
  const facts = await target.evaluate((node, token) => {
    const range = document.createRange(); range.selectNodeContents(node);
    const rects = [...range.getClientRects()];
    const frame = node.getBoundingClientRect();
    const probe = document.createElement("span"); probe.style.color = `var(${token})`; node.append(probe);
    const expectedColor = getComputedStyle(probe).color; probe.remove();
    return { lines: new Set(rects.map(rect => rect.top)).size, color: getComputedStyle(node).color, expectedColor,
      fontSize: getComputedStyle(node).fontSize, whole: rects.every(rect => rect.left >= frame.left && rect.right <= frame.right + 1) };
  }, colorToken);
  if (singleLine) expect(facts.lines).toBe(1);
  expect(facts.whole).toBe(true);
  expect(facts.fontSize).toBe("12px");
  expect(facts.color).toBe(facts.expectedColor);
}

test.describe("necessary hover hints and visible consequences", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1280, height: 1000 }, reducedMotion: "reduce" });
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
  });

  test("changes the mode consequence, keeps section names quiet and explains maintenance data flow from its heading", async ({ page }) => {
    await open(page, "#buddies/router");
    const mode = page.getByRole("group", { name: "默认模式", exact: true });
    const modeHelp = mode.locator('[data-slot="router-setting-help"]');
    const fast = mode.getByRole("radio", { name: "快速", exact: true });
    const review = mode.getByRole("radio", { name: "审阅", exact: true });
    await fast.click();
    await expect(mode.locator('[data-slot="routing-mode-consequence"]')).toHaveCount(0);
    await modeHelp.click();
    await expect(page.getByRole("dialog", { name: "关于默认模式" })).toContainText("需要 Router 判断时，任务的文字会发给 Router 的模型提供方");
    await modeHelp.click();
    await capture(page, "router-fast");
    await review.click();
    await modeHelp.click();
    await expect(page.getByRole("dialog", { name: "关于默认模式" })).toContainText("还会发送一份冻结的代码副本");
    await expect(page.getByRole("dialog", { name: "关于默认模式" })).toContainText("DSH 与 ZCode 没有系统沙盒");
    await modeHelp.click();
    await capture(page, "router-review");
    const responseWait = page.getByRole("group", { name: "开始作答时限", exact: true });
    const retry = page.getByRole("group", { name: "重试间隔", exact: true });
    const list = page.getByRole("list", { name: "Router 列表" });
    await quiet(page, [mode.getByText("默认模式", { exact: true }), fast, review,
      responseWait.getByText("开始作答时限", { exact: true }), retry.getByText("重试间隔", { exact: true }),
      list.getByRole("button").nth(1), list.getByRole("button").nth(2)]);
    await page.getByRole("navigation", { name: "Router 的工作" }).getByRole("link", { name: /维护/ }).click();
    const maintenance = page.getByRole("group", { name: "维护 buddy", exact: true });
    await expect(maintenance.locator('[data-slot="maintenance-consequence"]')).toHaveCount(0);
    const help = maintenance.locator('[data-slot="router-setting-help"]');
    await help.click();
    await expect(page.getByRole("dialog", { name: "关于维护 buddy" })).toContainText("任务描述与结果事实");
    await capture(page, "maintenance");
    await page.keyboard.press("Escape");
    const domainBranch = page.locator('[data-slot="domain-tree"] [role="treeitem"]').first();
    await quiet(page, [domainBranch, page.locator('[data-slot="maintenance-record"] [aria-label="失败"]').first()]);
  });

  test("keeps locked choices' reasons without appending mode explanations", async ({ page }) => {
    await page.route("**/api/command", async route => {
      if (route.request().postDataJSON().operation !== "user_policy_publish") return route.fallback();
      await route.fulfill({ json: { ok: false, error: { code: "CONSOLE_SESSION_EXPIRED", message: "synthetic expired session" } } });
    });
    await open(page, "#buddies/router");
    await page.getByRole("radio", { name: "审阅", exact: true }).click();
    await page.getByRole("button", { name: "保存", exact: true }).click();
    for (const target of [page.getByRole("radio", { name: "快速", exact: true }),
      page.getByRole("spinbutton", { name: "开始作答时限" }),
      page.getByRole("spinbutton", { name: "重试间隔秒数" })]) {
      await target.hover();
      await expect(page.getByRole("tooltip")).toHaveText("浏览器授权已失效，重新授权后可以修改");
      await page.mouse.move(0, 0);
    }
  });

  test("keeps ordinary marks, folds and preferences quiet and explains missing reads and unavailable reasons", async ({ page }) => {
    await open(page, "#buddies/catalog");
    await quiet(page, [page.locator('[data-slot="fold-handle"]').first(),
      page.locator('[data-slot="model-row"] .rounded-full').first()]);
    const missing = page.locator('[data-slot="model-row"]').getByText("待确认", { exact: true });
    await missing.hover();
    await expect(page.getByRole("tooltip")).toContainText("首次缺席满一小时后复查");
    await expect(page.getByRole("tooltip")).toContainText("无需操作");
    await expect(page.getByRole("tooltip")).toContainText("2026");
    await page.mouse.move(0, 0);
    await page.getByRole("button", { name: "codex / openai / gpt-5-codex", exact: true }).click();
    const drawer = page.locator('[data-slot="model-drawer"]');
    await quiet(page, [drawer.getByRole("radio", { name: "优先", exact: true }),
      drawer.getByRole("radio", { name: "固定", exact: true }), drawer.getByRole("radio", { name: "排除", exact: true })]);
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
    const ordinary = storage.getByRole("row").filter({ hasText: "保留的 harness 目录" });
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
    await expect(page.getByRole("tooltip", { name: /^滚轮或 \+ \/ − 缩放/ })).toBeVisible();
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

async function expectHelpPlacement(button: Locator, dialog: Locator, width: number) {
  await expect(dialog).toBeVisible();
  const placement = await button.evaluate(node => {
    const anchor = node.closest('[data-slot="popover-anchor"]')!;
    const label = anchor.querySelector("[id]")!.getBoundingClientRect();
    const row = node.closest("h3") ?? node.closest('[role="group"]')!;
    const ownContents = [label, node.getBoundingClientRect(), ...[...row.querySelectorAll('input, [role="radio"]')].map(control => control.getBoundingClientRect())];
    return { left: label.left, bottom: Math.max(...ownContents.map(box => box.bottom)) };
  });
  const overlay = (await dialog.boundingBox())!;
  expect(Math.abs(overlay.x - placement.left), "card starts at its own label").toBeLessThanOrEqual(1);
  expect(overlay.y, "card clears its own label, question mark and controls").toBeGreaterThanOrEqual(placement.bottom - 1);
  expect(overlay.x, "left viewport margin").toBeGreaterThanOrEqual(16);
  expect(overlay.x + overlay.width, "right viewport margin").toBeLessThanOrEqual(width - 16);
}

for (const locale of ["zh-CN", "en-US"]) for (const theme of ["dark", "light"]) for (const width of [1440, 390]) test.describe(`Router setting help ${locale} ${theme} ${width}px`, () => {
  test.use({ locale, viewport: { width, height: 1100 }, reducedMotion: "reduce" });
  test("anchors explanations below their own row with viewport margins and closes by keyboard or outside click", async ({ page }) => {
    const zh = locale === "zh-CN";
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, "#buddies/router/routing");
    const buttons = page.locator('[data-slot="router-setting-help"]');
    await expect(buttons).toHaveCount(4);
    const meanings = zh ? ["冻结的代码副本", "开始作答后不限时", "间隔过后，新路由", "样本更少的 buddy"]
      : ["frozen code copy", "no limit", "after the interval", "fewer samples"];
    for (const [index, meaning] of meanings.entries()) {
      await expect(buttons.nth(index)).toHaveAttribute("data-size", "icon-sm");
      await expect(buttons.nth(index)).toHaveAttribute("aria-expanded", "false");
      // Leave room for the preferred bottom placement; Radix may flip a card
      // when its row is at the viewport edge.
      await buttons.nth(index).locator('xpath=ancestor::*[@role="group"][1]').evaluate(node => node.scrollIntoView({ block: "center" }));
      await buttons.nth(index).click();
      const dialog = page.getByRole("dialog", { name: new RegExp(zh ? "关于" : "About") });
      await expect(dialog).toContainText(meaning);
      await expect(buttons.nth(index)).toHaveAttribute("aria-expanded", "true");
      await expectHelpPlacement(buttons.nth(index), dialog, width);
      if (zh && theme === "dark" && width === 1440 && index === 1) {
        const destination = fileURLToPath(new URL("../../../tmp/layout/", import.meta.url));
        await mkdir(destination, { recursive: true });
        await page.screenshot({ path: `${destination}router-help-dark-1440.png` });
      }
      const label = buttons.nth(index).locator('xpath=ancestor::*[@data-slot="popover-anchor"][1]').locator("[id]").first();
      await label.click();
      await expect(dialog).toHaveCount(0);
      await buttons.nth(index).click();
      await buttons.nth(index).click();
      await expect(dialog).toHaveCount(0);
      await buttons.nth(index).click();
      await page.keyboard.press("Escape");
      await expect(buttons.nth(index)).toHaveAttribute("aria-expanded", "false");
      await expect(buttons.nth(index)).toBeFocused();
    }
    const wait = page.getByRole("spinbutton", { name: zh ? "开始作答时限" : "Response start timeout" });
    await wait.focus();
    await page.keyboard.press("Shift+Tab");
    await expect(buttons.nth(1)).toBeFocused();
    await page.keyboard.press("Enter");
    const waitDialog = page.getByRole("dialog", { name: new RegExp(zh ? "关于开始作答时限" : "About Response start timeout") });
    await expect(waitDialog).toContainText(zh ? "本轮维护记为失败" : "maintenance run fails");
    await expect(waitDialog).toContainText(zh ? "画像构建记为失败" : "profile building fails");
    await expect(waitDialog).toContainText(zh ? "无法观察作答开始的 harness（如 DSH）" : "Harnesses that cannot observe the start (such as DSH)");
    await expect(waitDialog).toContainText(zh ? "由其自身的空闲超时处理" : "their own idle timeout");
    await buttons.nth(1).focus();
    await page.keyboard.press("Enter");
    await expect(waitDialog).toHaveCount(0);
    await expect(buttons.nth(1)).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(waitDialog).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(buttons.nth(1)).toBeFocused();
    await page.keyboard.press("Space");
    await expect(waitDialog).toBeVisible();
    await buttons.nth(1).focus();
    await page.keyboard.press("Space");
    await expect(waitDialog).toHaveCount(0);
    await page.keyboard.press("Space");
    await expect(waitDialog).toBeVisible();
    await wait.click();
    await expect(waitDialog).toHaveCount(0);

    for (const [work, title] of [["maintenance", zh ? "维护 buddy" : "Maintenance buddy"], ["profiles", zh ? "画像 buddy" : "Profile buddy"]]) {
      await page.locator(`[data-slot="router-work-toolbar"] a[href="#buddies/router/${work}"]`).click();
      const group = page.getByRole("group", { name: title, exact: true });
      const button = group.locator('[data-slot="router-setting-help"]');
      await button.click();
      const dialog = page.getByRole("dialog", { name: `${zh ? "关于" : "About "}${title}`, exact: true });
      await expectHelpPlacement(button, dialog, width);
      await page.keyboard.press("Escape");
      await expect(dialog).toHaveCount(0);
      await expect(button).toBeFocused();
      await button.click();
      await group.getByRole("heading").locator("[id]").click();
      await expect(dialog).toHaveCount(0);
    }

    await expect(page.locator('[data-slot="routing-mode-consequence"]')).toHaveCount(0);
    await expectNoSidewaysScroll(page);
  });
});
