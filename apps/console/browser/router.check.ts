import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { expect, type Locator, type Page } from "@playwright/test";
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
  { locale: "zh-CN", jobs: "Router 的工作", routing: "路由", maintenance: "维护", profiles: "画像", maintenanceBuddy: "维护 buddy", profileBuddy: "画像 buddy", settings: "设置", records: "记录", list: "Router 列表", wait: "开始作答时限", errors: "最近的失败", choose: "选择 buddy" },
  { locale: "en-US", jobs: "Router work", routing: "Routing", maintenance: "Maintenance", profiles: "Profiles", maintenanceBuddy: "Maintenance buddy", profileBuddy: "Profile buddy", settings: "Settings", records: "Records", list: "Router list", wait: "Response start timeout", errors: "Recent failures", choose: "Choose buddy" },
] as const;

for (const w of words) for (const theme of ["dark", "light"] as const) {
  test.describe(`Router work ${w.locale} ${theme} 1440px`, () => {
    test.use({ locale: w.locale, viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme));

    test("keeps three icon jobs and one detail, with ordered routing controls", async ({ page }) => {
      await open(page, "#buddies/router/routing");
      const region = page.getByRole("region", { name: "Router", exact: true });
      const nav = region.getByRole("navigation", { name: w.jobs });
      await expect(nav).toHaveAttribute("data-slot", "router-work-toolbar");
      const links = nav.getByRole("link");
      await expect(links).toHaveCount(3);
      for (const [index, name] of [w.routing, w.maintenance, w.profiles].entries()) {
        await expect(links.nth(index)).toContainText(name);
        await expect(links.nth(index).locator("svg")).toHaveCount(1);
        await expect(links.nth(index).locator("p, button, input")).toHaveCount(0);
        await expect(links.nth(index)).not.toContainText(/最近|未设置|Latest|Not set/);
      }
      await expect(links.first()).toHaveAttribute("aria-current", "page");
      const detail = region.locator('[data-slot="router-detail"]');
      await expect(detail.getByRole("heading", { name: w.routing, exact: true })).toHaveCount(0);
      await expect(detail.getByRole("group", { name: w.list, exact: true })).toBeVisible();
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
      await expect(page.getByRole("dialog", { name: new RegExp(w.wait) })).toContainText(/不限时|no limit/);
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
      await expect(detail.getByRole("heading", { name: w.maintenance, exact: true })).toHaveCount(0);
      await expect(detail.getByRole("group", { name: w.maintenanceBuddy, exact: true })).toBeVisible();
      await expect(detail.locator('[data-slot="routing-work"]')).toHaveCount(0);
      await links.nth(2).click();
      await expect(detail.getByRole("heading", { name: w.profiles, exact: true })).toHaveCount(0);
      await expect(detail.getByRole("group", { name: w.profileBuddy, exact: true })).toBeVisible();
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
      await expect(page.getByRole("dialog", { name: new RegExp(w.wait) })).toContainText(/不限时|no limit/);
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

for (const theme of ["dark", "light"] as const) for (const width of [1440, 390]) test.describe(`Router toolbar at ${width}px in ${theme}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 900 }, reducedMotion: "reduce" });
  test("follows the catalogue band", async ({ page }) => {
  await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
  await open(page, "#buddies/catalog");
  const catalogue = page.locator('[data-slot="catalogue-toolbar"]');
  const catalogueBox = (await catalogue.boundingBox())!;
  const catalogueStyle = await catalogue.evaluate(node => ({ padding: getComputedStyle(node).paddingLeft, border: getComputedStyle(node).borderBottomWidth, borderColor: getComputedStyle(node).borderBottomColor }));
  await page.locator('[data-slot="buddy-header"]').getByRole("link", { name: /^Router/ }).click();
  const toolbar = page.locator('[data-slot="router-work-toolbar"]');
  const toolbarBox = (await toolbar.boundingBox())!;
  const toolbarStyle = await toolbar.evaluate(node => ({ padding: getComputedStyle(node).paddingLeft, border: getComputedStyle(node).borderBottomWidth, borderColor: getComputedStyle(node).borderBottomColor }));
  expect(toolbarBox.y).toBe(catalogueBox.y);
  if (width === 1440) expect(toolbarBox.height).toBe(catalogueBox.height);
  expect(toolbarBox.x).toBe(catalogueBox.x);
  expect(toolbarBox.width).toBe(catalogueBox.width);
  expect(toolbarStyle).toEqual(catalogueStyle);
  const detail = page.locator('[data-slot="router-detail"]');
  const firstTitle = (await detail.getByRole("heading", { level: 3 }).first().boundingBox())!;
  expect(firstTitle.y - toolbarBox.y - toolbarBox.height, "first section starts 24px below the toolbar").toBe(24);
  const links = toolbar.getByRole("link");
  for (const link of await links.all()) {
    const block = (await link.boundingBox())!;
    expect(block.height).toBe(36);
    const style = await link.evaluate(node => ({ line: getComputedStyle(node).textDecorationLine, after: getComputedStyle(node, "::after").content }));
    expect(style.line).toBe("none");
    expect(style.after).toBe("none");
  }
  const background = await links.first().evaluate(node => getComputedStyle(node).backgroundColor);
  expect(background).not.toBe(await links.nth(1).evaluate(node => getComputedStyle(node).backgroundColor));
  await links.nth(1).click();
  expect(await links.nth(1).evaluate(node => getComputedStyle(node).backgroundColor)).toBe(background);
  const maintenanceTitle = (await detail.getByRole("heading", { level: 3 }).first().boundingBox())!;
  expect(maintenanceTitle.y - toolbarBox.y - toolbarBox.height).toBe(24);
  const viewport = toolbar.locator("..").locator(':scope > [data-slot="scroll-area"] > [data-slot="scroll-area-viewport"]');
  await viewport.evaluate(node => { node.scrollTop = node.scrollHeight; });
  expect((await toolbar.boundingBox())!.y).toBe(toolbarBox.y);
  await links.nth(2).focus();
  await page.keyboard.press("Enter");
  await expect(links.nth(2)).toBeFocused();
  expect(await viewport.evaluate(node => node.scrollTop)).toBe(0);
  const profileTitle = (await detail.getByRole("heading", { level: 3 }).first().boundingBox())!;
  expect(profileTitle.y - toolbarBox.y - toolbarBox.height).toBe(24);
  await expectNoSidewaysScroll(page);
  });

  test("joins each selected and hovered job to the toolbar border without a gap", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, "#buddies/router/routing");
    const toolbar = page.locator('[data-slot="router-work-toolbar"]');
    const links = toolbar.getByRole("link");
    const band = (await toolbar.boundingBox())!;
    const border = await toolbar.evaluate(node => parseFloat(getComputedStyle(node).borderBottomWidth));
    const borderTop = band.y + band.height - border;
    const touchesBorder = async (link: Locator) => {
      const block = (await link.boundingBox())!;
      expect(block.height).toBe(36);
      expect(block.y + block.height, "the job background reaches the top of the border").toBe(borderTop);
    };
    for (const [index, link] of (await links.all()).entries()) {
      const corners = await link.evaluate(node => {
        const style = getComputedStyle(node);
        return [style.borderTopLeftRadius, style.borderTopRightRadius, style.borderBottomLeftRadius, style.borderBottomRightRadius].map(parseFloat);
      });
      expect(corners[0]).toBeGreaterThan(0);
      expect(corners[1]).toBeGreaterThan(0);
      expect(corners.slice(2)).toEqual([0, 0]);
      await link.click();
      await page.mouse.move(0, 0);
      await expect(link).toHaveAttribute("aria-current", "page");
      expect(await link.evaluate(node => getComputedStyle(node).backgroundColor)).not.toBe("rgba(0, 0, 0, 0)");
      await touchesBorder(link);
      const hovered = links.nth((index + 1) % 3);
      const resting = await hovered.evaluate(node => getComputedStyle(node).backgroundColor);
      await hovered.hover();
      expect(await hovered.evaluate(node => getComputedStyle(node).backgroundColor)).not.toBe(resting);
      await touchesBorder(hovered);
    }
  });
});

for (const w of words) for (const theme of ["dark", "light"] as const) test.describe(`unset buddy guidance in ${w.locale} ${theme}`, () => {
  test.use({ locale: w.locale, viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  test("stays in its section", async ({ page }) => {
  await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
  await page.route("**/api/console", async route => {
    const response = await route.fetch(); const snapshot = await response.json();
    snapshot.configuration.maintenanceBuddyId = null;
    snapshot.configuration.profileBuddyId = null;
    await route.fulfill({ response, json: snapshot });
  });
  await open(page, "#buddies/router/maintenance");
  const detail = page.locator('[data-slot="router-detail"]');
  const missing = w.locale === "zh-CN" ? "此项工作不运行，请指定 buddy。" : "This work does not run. Set a buddy.";
  const maintenance = detail.getByRole("group", { name: w.maintenanceBuddy, exact: true });
  await expect(maintenance.getByText(missing)).toHaveCount(1);
  await expect(detail.getByText(missing)).toHaveCount(1);
  await expect(maintenance.locator('[data-slot="maintenance-consequence"]')).toHaveCount(0);
  const maintenanceHelp = maintenance.locator('[data-slot="router-setting-help"]');
  await maintenanceHelp.click();
  const maintenanceDialog = page.getByRole("dialog");
  await expect(maintenanceDialog).toContainText(w.locale === "zh-CN" ? "任务描述与结果事实" : "descriptions and outcome facts");
  const bubble = (await maintenanceDialog.boundingBox())!;
  const trigger = (await maintenanceHelp.boundingBox())!;
  const buddyHeader = (await page.locator('[data-slot="buddy-header"]').boundingBox())!;
  expect(bubble.y).toBeGreaterThanOrEqual(trigger.y + trigger.height);
  expect(bubble.y).toBeGreaterThanOrEqual(buddyHeader.y + buddyHeader.height);
  await page.keyboard.press("Escape");
  await page.locator('[data-slot="router-work-toolbar"]').getByRole("link", { name: new RegExp(w.profiles) }).click();
  const profile = detail.getByRole("group", { name: w.profileBuddy, exact: true });
  await expect(profile.getByText(missing)).toHaveCount(1);
  await expect(detail.getByText(missing)).toHaveCount(1);
  await expect(profile.locator('[data-slot="profile-consequence"]')).toHaveCount(0);
  await profile.locator('[data-slot="router-setting-help"]').click();
  await expect(page.getByRole("dialog")).toContainText(w.locale === "zh-CN" ? "模型名称与对应关系候选" : "model names and correspondence candidates");
  });
});

test.describe("Router toolbar attention", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
for (const theme of ["dark", "light"] as const) for (const variant of ["success", "failed", "unset"] as const) test(`shows ${variant} routing attention in ${theme}`, async ({ page }) => {
  await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
  await page.route("**/api/console", async route => {
    const response = await route.fetch(); const snapshot = await response.json();
    if (variant === "success") {
      snapshot.routingHealth.recentFailures = [];
      snapshot.routingHealth.consecutiveFailures = 0;
      for (const router of snapshot.routingHealth.routers) router.lastError = null;
      snapshot.maintenance.runs = snapshot.maintenance.runs.filter((run: { state: string }) => run.state === "completed");
      for (const model of snapshot.models) if (model.latestProfileRun) { model.latestProfileRun.state = "completed"; model.latestProfileRun.reason = null; }
    }
    if (variant === "unset") {
      snapshot.configuration.routerBuddyIds = [];
      snapshot.routingHealth.currentRouterBuddyId = null;
      snapshot.configuration.maintenanceBuddyId = null;
      snapshot.configuration.profileBuddyId = null;
    }
    if (variant === "failed") snapshot.routingHealth.consecutiveFailures = 2;
    await route.fulfill({ response, json: snapshot });
  });
  await open(page, "#buddies/router/routing");
  const nav = page.locator('[data-slot="router-work-toolbar"]');
  const links = nav.getByRole("link");
  await expect(links).toHaveCount(3);
  await expect(links.nth(0).locator(".bg-hand-mark")).toHaveCount(variant === "success" ? 0 : 1);
  await expect(links.nth(1).locator(".bg-hand-mark")).toHaveCount(0);
  await expect(links.nth(2).locator(".bg-hand-mark")).toHaveCount(0);
  for (const link of await links.all()) await expect(link).not.toContainText(/最近|未设置|Latest|Not set|deepseek|dsh/);
  if (variant !== "success") {
    const description = (await links.first().getAttribute("aria-description"))!;
    const [work, reason] = description.split("\n");
    expect(work).toBe("在路由边界内，为一个微任务选择 Worker buddy。");
    expect(reason.length).toBeGreaterThan(0);
    await links.first().hover();
    await expect(page.getByRole("tooltip")).toContainText(reason);
    await expect(page.locator('[data-slot="tooltip-content"]')).toHaveCSS("white-space", "pre-line");
    const lines = await page.locator('[data-slot="tooltip-content"]').evaluate((node, [work, reason]) => {
      const text = node.firstChild!;
      const range = document.createRange();
      range.setStart(text, work.length - 1); range.setEnd(text, work.length);
      const workBox = range.getBoundingClientRect();
      range.setStart(text, work.length + 1); range.setEnd(text, work.length + 1 + reason.length);
      return { workBottom: workBox.bottom, reasonTop: range.getBoundingClientRect().top };
    }, [work, reason]);
    expect(lines.reasonTop).toBeGreaterThanOrEqual(lines.workBottom);
    await page.mouse.move(0, 0);
    await expect(page.getByRole("tooltip")).toHaveCount(0);
  }
  await links.nth(1).hover();
  await expect(page.getByRole("tooltip")).toContainText("将已结束的微任务归入领域树与任务类型");
  const tip = (await page.getByRole("tooltip").boundingBox())!;
  const trigger = (await links.nth(1).boundingBox())!;
  const buddyHeader = (await page.locator('[data-slot="buddy-header"]').boundingBox())!;
  expect(tip.y).toBeGreaterThanOrEqual(trigger.y + trigger.height - 1);
  expect(tip.y).toBeGreaterThanOrEqual(buddyHeader.y + buddyHeader.height);
  await page.mouse.move(0, 0);
  await links.nth(1).focus();
  await page.keyboard.press("Tab");
  await expect(links.nth(2)).toBeFocused();
  await expect(page.getByRole("tooltip")).toContainText("联网查找公开资料");
});
});

test.describe("maintenance domain outline", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  for (const theme of ["dark", "light"] as const) test(`aligns three columns and opens details from the info button in ${theme}`, async ({ page }) => {
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
      .map(node => node.querySelector(".domain-tree-name-text")!.getBoundingClientRect().x));
    expect(new Set(rootNames.map(Math.round)).size).toBe(1);

    const parentBar = tree.locator('[data-node="preview-domain-console"].domain-tree-row .domain-tree-bar');
    await expect(parentBar.locator(".domain-tree-segment")).toHaveCount(2);
    const parentLeaf = parentBar.locator('[data-segment="preview-domain-tokens"]');
    const leaf = tree.locator('[data-node="preview-domain-tokens"].domain-tree-row [data-segment="preview-domain-tokens"]');
    expect(await parentLeaf.evaluate(node => getComputedStyle(node).backgroundColor))
      .toBe(await leaf.evaluate(node => getComputedStyle(node).backgroundColor));
    await expect(parentBar.locator(".domain-tree-bar-fill")).toHaveCSS("column-gap", "3px");

    const child = rows.filter({ hasText: "样式与排版" });
    const childInfo = child.locator(".domain-tree-info");
    await expect(childInfo).toHaveCSS("opacity", "0");
    await child.hover();
    await expect(childInfo).toHaveCSS("opacity", "1");
    await expect(parentBar.locator('[data-segment="preview-domain-tokens"]')).toHaveAttribute("data-highlight", "true");
    await expect(page.locator('[data-slot="domain-details"]')).toHaveCount(0);
    await childInfo.click();
    const panel = page.locator('[data-slot="domain-details"]');
    await expect(panel).toBeVisible();
    await expect(panel).toContainText("样式与排版");
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(childInfo).toBeFocused();
    const parent = rows.filter({ hasText: "控制台" }).first();
    await parent.focus();
    await expect(parent.locator(".domain-tree-info")).toHaveCSS("opacity", "1");
    await parent.locator(".domain-tree-info").click();
    await expect(panel).toContainText("旧控制台界面");
    await expect(panel).toContainText("前端界面");
    await captureWork(page, `maintenance-hover-${theme}-1440.png`);
    await page.locator('[data-slot="buddy-header"]').getByRole("status").click();
    await expect(panel).toHaveCount(0);
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
