import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { expect, type Page } from "@playwright/test";
import { expectNoSidewaysScroll, open, settle, test } from "./preview";

const captures = fileURLToPath(new URL("../../../tmp/redo-b3/", import.meta.url));
async function captureWork(page: Page, name: string) {
  const contentHeight = await page.locator('[data-slot="router-detail"]').evaluate(node => node.scrollHeight);
  await page.setViewportSize({ width: 1440, height: Math.max(900, contentHeight + 160) });
  await settle(page);
  await mkdir(captures, { recursive: true });
  await page.screenshot({ path: join(captures, name), fullPage: true });
}
const words = [
  { locale: "zh-CN", jobs: "Router 的工作", routing: "路由", maintenance: "维护", profiles: "画像", who: "谁担任", settings: "设置", records: "记录", list: "Router 列表", wait: "等待模型开始作答", errors: "最近的失败", choose: "选择 buddy" },
  { locale: "en-US", jobs: "Router work", routing: "Routing", maintenance: "Maintenance", profiles: "Profiles", who: "Who serves", settings: "Settings", records: "Records", list: "Router list", wait: "Wait for the model to start responding", errors: "Recent failures", choose: "Choose buddy" },
] as const;

for (const w of words) for (const theme of ["dark", "light"] as const) {
  test.describe(`Router work ${w.locale} ${theme} 1440px`, () => {
    test.use({ locale: w.locale, viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme));

    test("keeps three name/status jobs and one detail, with ordered routing controls", async ({ page }) => {
      await open(page, "#buddies/router/routing");
      const region = page.getByRole("region", { name: "Router" });
      const nav = region.getByRole("navigation", { name: w.jobs });
      const links = nav.getByRole("link");
      await expect(links).toHaveCount(3);
      for (const [index, name] of [w.routing, w.maintenance, w.profiles].entries()) {
        await expect(links.nth(index)).toContainText(name);
        await expect(links.nth(index).locator("p, button, input")).toHaveCount(0);
      }
      await expect(links.first()).toHaveAttribute("aria-current", "page");
      const detail = region.locator('[data-slot="router-detail"]');
      await expect(detail.getByRole("heading", { name: w.routing, exact: true })).toBeVisible();
      await expect(detail.getByRole("group", { name: w.who, exact: true })).toBeVisible();
      await expect(detail.getByRole("group", { name: w.settings, exact: true })).toBeVisible();
      await expect(detail.getByRole("group", { name: w.records, exact: true })).toBeVisible();
      const lines = detail.getByRole("list", { name: w.list }).getByRole("listitem");
      await expect(lines).toHaveCount(3);
      await expect(lines.nth(0)).toContainText("1");
      await expect(lines.nth(1)).toContainText("2");
      await expect(lines.nth(2)).toContainText("3");
      await expect(detail.getByRole("spinbutton", { name: w.wait })).toHaveValue("60");
      await expect(detail.locator("[data-slot=router-setting-help]")).toHaveCount(4);
      const waitHelp = detail.getByRole("group", { name: w.wait, exact: true }).locator("[data-slot=router-setting-help]");
      await waitHelp.click();
      await expect(page.getByRole("dialog", { name: new RegExp(w.wait) })).toContainText(/不限时|no time limit/);
      await waitHelp.click();
      const plots = detail.getByRole("table", { name: w.locale === "zh-CN" ? "各 Router 的记录" : "Records of each Router" }).locator('[data-slot="quant-bar"]');
      await expect(plots).toHaveCount(3);
      await expect(plots.first().locator('rect[data-tone="green"]')).toHaveCount(1);
      await expect(plots.first().locator('rect[data-tone="pink"]')).toHaveCount(1);
      const rows = detail.getByRole("table", { name: w.errors }).getByRole("row");
      await expect(rows).toHaveCount(2);
      await expect(rows.nth(1)).toContainText(w.locale === "zh-CN" ? "选择超出路由边界" : "Selection outside routing bounds");
      await expect(rows.nth(1).getByRole("link")).toHaveAttribute("href", /#tasks\/.*\/preview-run-a2$/);
      await expect(rows.nth(1).getByRole("button", { name: w.locale === "zh-CN" ? "复制编号" : "Copy ID" })).toBeVisible();
      await expectNoSidewaysScroll(page);
      if (w.locale === "zh-CN") await captureWork(page, `router-${theme}-1440.png`);
      await links.nth(1).click();
      await expect(links.nth(1)).toHaveAttribute("aria-current", "page");
      await expect(detail.getByRole("heading", { name: w.maintenance, exact: true })).toBeVisible();
      await expect(detail.locator('[data-slot="routing-work"]')).toHaveCount(0);
      await links.nth(2).click();
      await expect(detail.getByRole("heading", { name: w.profiles, exact: true })).toBeVisible();
      await expect(detail.locator('[data-slot="maintenance-plane"]')).toHaveCount(0);
      await expect(detail.getByRole("button", { name: w.choose })).toBeVisible();
    });

    test("keeps routing settings distinct and the profile failure expanded", async ({ page }) => {
      await open(page, "#buddies/router/routing");
      const detail = page.locator('[data-slot="router-detail"]');
      const settings = detail.getByRole("group", { name: w.settings, exact: true });
      await expect(settings.getByRole("spinbutton", { name: w.wait })).toHaveValue("60");
      const waitHelp = settings.getByRole("group", { name: w.wait, exact: true }).locator("[data-slot=router-setting-help]");
      await waitHelp.click();
      await expect(page.getByRole("dialog", { name: new RegExp(w.wait) })).toContainText(/不限时|no time limit/);
      await page.keyboard.press("Escape");
      await expect(waitHelp).toHaveAttribute("aria-expanded", "false");
      await page.getByRole("navigation", { name: w.jobs }).getByRole("link", { name: new RegExp(w.profiles) }).click();
      await expect(detail.getByRole("spinbutton", { name: w.wait })).toHaveCount(0);
      const failureButton = detail.locator('[data-slot="profile-records"] tr button').filter({ hasText: "glm-5" });
      await expect(failureButton).toHaveAttribute("aria-expanded", "true");
      const failure = detail.locator('[data-slot="profile-records"] tr:has(> td > button:has-text("glm-5")) + tr');
      await expect(failure).toBeVisible();
      await expect(failure.getByRole("alert")).toBeVisible();
      await expect(failure.getByRole("table")).toHaveCount(2);
      if (w.locale === "zh-CN") await captureWork(page, `profiles-failed-${theme}-1440.png`);
    });
  });
}

test.describe("Router navigation facts", () => {
  test.use({ locale: "zh-CN" });
for (const variant of ["success", "failed", "unset"] as const) test(`Router navigation reports ${variant} work from synthetic facts`, async ({ page }) => {
  await page.route("**/api/console", async route => {
    const response = await route.fetch(); const snapshot = await response.json();
    if (variant === "success") {
      snapshot.routingHealth.recentFailures = [];
      for (const router of snapshot.routingHealth.routers) router.lastError = null;
      snapshot.maintenance.runs = snapshot.maintenance.runs.filter((run: { state: string }) => run.state === "completed");
      for (const model of snapshot.models) if (model.latestProfileRun) { model.latestProfileRun.state = "completed"; model.latestProfileRun.reason = null; }
    }
    if (variant === "unset") {
      snapshot.configuration.routerBuddyIds = [];
      snapshot.configuration.maintenanceBuddyId = null;
      snapshot.configuration.profileBuddyId = null;
    }
    await route.fulfill({ response, json: snapshot });
  });
  await open(page, "#buddies/router/routing");
  const nav = page.getByRole("navigation", { name: "Router 的工作" });
  const links = nav.getByRole("link");
  const expected = variant === "unset" ? ["未设置", "未设置", "未设置"]
    : variant === "success" ? ["最近一次 · 成功", "最近一轮 · 已完成", "最近一次 · 成功"]
      : ["最近一次 · 失败", "最近一轮 · 已完成", "最近一次 · 失败"];
  for (const [index, status] of expected.entries()) await expect(links.nth(index)).toContainText(status);
  await expect(nav).not.toContainText("deepseek");
  await expect(nav).not.toContainText("dsh");
});
});

test.describe("maintenance domain outline", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  for (const theme of ["dark", "light"] as const) test(`aligns three columns and keeps the hover panel clear in ${theme}`, async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, "#buddies/router/maintenance");
    const tree = page.locator('[data-slot="domain-tree"]');
    const rows = tree.getByRole("treeitem");
    await expect(rows).toHaveCount(4);
    await tree.getByRole("button", { name: "全部展开" }).click();
    await expect(rows).toHaveCount(9);
    await expect(tree).toContainText("4 个顶层领域 · 9 个节点 · 28 个微任务");
    expect(await rows.evaluateAll(nodes => nodes.filter(node => node.getAttribute("aria-level") === "1").map(node => node.getAttribute("data-node"))))
      .toEqual(["preview-domain-ui", "preview-domain-science", "preview-domain-docs", "preview-domain-statistics"]);
    const columns = await rows.evaluateAll(nodes => nodes.map(node => [".domain-tree-name", ".domain-tree-count", ".domain-tree-bar"].map(selector => node.querySelector(selector)!.getBoundingClientRect().x)));
    for (let column = 1; column < 3; column++) expect(new Set(columns.map(row => Math.round(row[column]!)))).toHaveProperty("size", 1);
    expect(columns.every(row => row[0]! < row[1]! && row[1]! < row[2]!)).toBe(true);
    const rootNames = await rows.evaluateAll(nodes => nodes.filter(node => node.getAttribute("aria-level") === "1")
      .map(node => node.querySelector(".domain-tree-name")!.lastElementChild!.getBoundingClientRect().x));
    expect(new Set(rootNames.map(Math.round)).size).toBe(1);

    const parentBar = tree.locator('[data-node="preview-domain-console"].domain-tree-row .domain-tree-bar');
    await expect(parentBar.locator(".domain-tree-segment")).toHaveCount(2);
    const parentLeaf = parentBar.locator('[data-segment="preview-domain-tokens"]');
    const leaf = tree.locator('[data-node="preview-domain-tokens"].domain-tree-row [data-segment="preview-domain-tokens"]');
    expect(await parentLeaf.evaluate(node => getComputedStyle(node).backgroundColor))
      .toBe(await leaf.evaluate(node => getComputedStyle(node).backgroundColor));
    await expect(parentBar.locator(".domain-tree-bar-fill")).toHaveCSS("column-gap", "3px");

    await rows.filter({ hasText: "样式与排版" }).hover();
    await expect(parentBar.locator('[data-segment="preview-domain-tokens"]')).toHaveAttribute("data-highlight", "true");
    const panel = tree.locator(".domain-tree-popover");
    await expect(panel).toBeVisible();
    await rows.filter({ hasText: "控制台" }).first().hover();
    await expect(panel).toContainText("旧控制台界面");
    await expect(panel).toContainText("前端界面");
    await settle(page);
    const overlaps = await tree.evaluate(node => {
      const panel = node.querySelector(".domain-tree-popover")!.getBoundingClientRect();
      return [...node.querySelectorAll(".domain-tree-name")].filter(label => { const box = label.getBoundingClientRect(); return box.left < panel.right && box.right > panel.left && box.top < panel.bottom && box.bottom > panel.top; }).map(label => label.textContent);
    });
    expect(overlaps).toEqual([]);
    await captureWork(page, `maintenance-hover-${theme}-1440.png`);
    await page.mouse.move(0, 0);
    await expect(panel).toBeHidden();
    await tree.getByRole("button", { name: "全部收起" }).click();
    await expect(rows).toHaveCount(4);
    await tree.getByRole("button", { name: "全部展开" }).click();
    await expect(rows).toHaveCount(9);
    const science = rows.filter({ hasText: "科研计算" });
    await science.focus();
    await page.keyboard.press("Enter");
    await expect(science).toHaveAttribute("aria-expanded", "false");
    await expect(rows).toHaveCount(8);
    await page.keyboard.press("Enter");
    await expect(rows).toHaveCount(9);
    await expectNoSidewaysScroll(page);
  });

  test("scrolls deep data locally without capping depth", async ({ page }) => {
    await page.route("**/api/console", async route => {
      const response = await route.fetch(); const snapshot = await response.json();
      let parent = "preview-domain-tokens";
      for (let depth = 0; depth < 60; depth++) {
        const nodeId = `synthetic-deep-${depth}`;
        snapshot.domainNodes.push({ nodeId, name: `第 ${depth + 4} 层`, definition: "合成预览", parentId: parent, mergedIntoId: null, directCount: 1 });
        parent = nodeId;
      }
      await route.fulfill({ response, json: snapshot });
    });
    await open(page, "#buddies/router/maintenance");
    const tree = page.locator('[data-slot="domain-tree"]');
    await tree.getByRole("button", { name: "全部展开" }).click();
    await expect(tree.getByRole("treeitem")).toHaveCount(69);
    await expect(tree.getByRole("treeitem", { name: /第 63 层/ })).toHaveAttribute("aria-level", "63");
    const viewport = tree.locator(".domain-tree-viewport");
    const size = await viewport.evaluate(node => ({ client: node.clientWidth, scroll: node.scrollWidth }));
    expect(size.scroll).toBeGreaterThan(size.client);
    await viewport.evaluate(node => { node.scrollLeft = node.scrollWidth; });
    await tree.getByRole("treeitem", { name: /第 63 层/ }).scrollIntoViewIfNeeded();
    await expect(tree.getByRole("treeitem", { name: /第 63 层/ })).toBeInViewport();
    await expectNoSidewaysScroll(page);
  });
});
