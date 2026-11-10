import { execFileSync } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";
import { expectNoSidewaysScroll, expectTextUncut, open, settle, test } from "./preview";

const repository = fileURLToPath(new URL("../../..", import.meta.url));
const destination = join(repository, "tmp/buddy-fix");
// Use the same synthetic generator as the manual `--scenario domains` preview.
const denseTree = JSON.parse(execFileSync("uv", ["run", "--frozen", "python", "-c",
  "import json,sys; sys.path.insert(0,'tests/probes'); import macro_task_console_preview as p; print(json.dumps(p.large_domain_tree()))"],
{ cwd: repository, encoding: "utf8" }));
async function capture(page: Page, name: string, theme: string) {
  await settle(page);
  await mkdir(destination, { recursive: true });
  await page.screenshot({ path: join(destination, `${name}-${theme}-1440.png`), fullPage: true });
}
for (const theme of ["dark", "light"] as const) test.describe(`Buddy graphics ${theme}`, () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 1100 }, reducedMotion: "reduce" });
  test.beforeEach(async ({ page }) => page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme));

  test("keeps a dense outline aligned and opens details below its Info button", async ({ page }) => {
    await page.route("**/api/console", async route => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), domainNodes: denseTree } });
    });
    await page.setViewportSize({ width: 1440, height: 2500 });
    await open(page, "#buddies/router/maintenance");
    const tree = page.locator('[data-slot="domain-tree"]');
    const rows = tree.getByRole("treeitem");
    await expect(rows).toHaveCount(41);
    await expect(tree).toContainText("41 个顶层领域 · 81 个节点");
    expect(await rows.evaluateAll(nodes => nodes.map(node => Number(node.querySelector(".domain-tree-count")!.textContent))))
      .toEqual([139, 103, 89, 75, 74, 67, 51, 44, 41, 37, 28, 24, 20, 18, 17, 17, 16, 15, 15, 14, 14, 13, 12, 12, 10, 10, 8, 8, 7, 6, 5, 5, 3, 3, 3, 3, 2, 1, 1, 1, 1]);
    await expectTextUncut(tree.locator(".domain-tree-name-text"));
    await capture(page, "domains-collapsed", theme);
    const first = rows.first();
    await first.click();
    await expect(rows).toHaveCount(47);
    const parentSegment = first.locator('[data-segment="preview-outline-01-2"]');
    const childSegment = tree.locator('[data-node="preview-outline-01-2"].domain-tree-row [data-segment="preview-outline-01-2"]');
    expect(await parentSegment.evaluate(node => getComputedStyle(node).backgroundColor))
      .toBe(await childSegment.evaluate(node => getComputedStyle(node).backgroundColor));
    const gaps = await tree.locator(".domain-tree-bar-fill").evaluateAll(bars => bars.flatMap(bar => {
      const segments = [...bar.children];
      return segments.slice(1).map((segment, index) => segment.getBoundingClientRect().left - segments[index].getBoundingClientRect().right);
    }));
    expect(gaps.every(gap => gap >= 2.5)).toBe(true);

    await tree.locator(".domain-tree-overview p").click();
    await page.mouse.move(0, 0);
    const child = rows.nth(1);
    const info = child.locator(".domain-tree-info");
    await expect(info).toHaveCSS("opacity", "0");
    await expect(info).toHaveCSS("pointer-events", "none");
    await expect(page.locator('[data-slot="domain-details"]')).toHaveCount(0);
    await capture(page, "domains-one-expanded", theme);
    await child.hover();
    await expect(info).toHaveCSS("opacity", "1");
    await expect(first.locator('[data-segment="preview-outline-01-1"]')).toHaveAttribute("data-highlight", "true");
    await page.mouse.move(0, 0);
    await expect(info).toHaveCSS("opacity", "0");
    await child.focus();
    await expect(info).toHaveCSS("opacity", "1");
    await page.keyboard.press("Tab");
    await expect(info).toBeFocused();
    await expect(info).toHaveCSS("opacity", "1");
    await page.keyboard.press("Tab");
    await expect(rows.nth(2)).toBeFocused();
    await child.hover();
    const hoverBackground = await child.evaluate(node => getComputedStyle(node).backgroundColor);
    const beforeOpen = await rows.count();
    await info.click();
    await expect(rows).toHaveCount(beforeOpen);
    await expect(child).toHaveAttribute("data-details-open", "true");
    const panel = page.locator('[data-slot="domain-details"]');
    await expect(panel).toBeVisible();
    await expect(panel).toHaveClass(/domain-tree-popover/);
    const infoBox = (await info.boundingBox())!;
    const nameBox = (await child.locator(".domain-tree-name-text").boundingBox())!;
    const panelBox = (await panel.boundingBox())!;
    expect(panelBox.y).toBeGreaterThanOrEqual(infoBox.y + infoBox.height - 1);
    expect(panelBox.y).toBeGreaterThanOrEqual(nameBox.y + nameBox.height - 1);
    await page.mouse.move(0, 0);
    expect(await child.evaluate(node => getComputedStyle(node).backgroundColor)).toBe(hoverBackground);
    await expect(first.locator('[data-segment="preview-outline-01-1"]')).toHaveAttribute("data-highlight", "true");
    await capture(page, "domains-hover", theme);
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(info).toBeFocused();
    await info.click();
    await expect(panel).toBeVisible();
    await tree.locator(".domain-tree-overview p").click();
    await expect(panel).toHaveCount(0);
    await expect(info).toHaveCSS("opacity", "0");
    await child.focus();
    await child.press("Enter");
    await expect(rows).toHaveCount(52);
    await child.press("Space");
    await expect(rows).toHaveCount(47);
    await child.press("Enter");
    await expect(rows).toHaveCount(52);
    await page.reload();
    await expect(rows).toHaveCount(52);
    await tree.getByRole("button", { name: "全部展开" }).click();
    await expect(rows).toHaveCount(81);
    await tree.getByRole("button", { name: "全部收起" }).click();
    await expect(rows).toHaveCount(41);
    await expectNoSidewaysScroll(page);
  });

  test("reveals exact values from the graphics and preserves each record's choice", async ({ page }) => {
    await open(page, "#buddies/evaluation/dsh/00000000-0000-4000-8000-000000000001/max");
    const sample = page.locator('[data-slot="evaluation-tally"]').first();
    const trigger = sample.locator('[data-slot="quant-bar-trigger"]');
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    await expect(trigger.locator('[data-slot="quant-bar"] rect[data-tone]')).toHaveCount(3);
    await capture(page, "evaluation-default", theme);
    const border = await trigger.evaluate(node => getComputedStyle(node).borderColor);
    await trigger.hover();
    expect(await trigger.evaluate(node => getComputedStyle(node).cursor)).toBe("pointer");
    expect(await trigger.evaluate(node => getComputedStyle(node).borderColor)).not.toBe(border);
    await capture(page, "evaluation-hover", theme);
    await trigger.press("Enter");
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    await expect(sample.locator('[data-slot="quant-bar-detail"]')).toContainText("中位数");
    await capture(page, "evaluation-expanded", theme);
    await page.reload();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    await trigger.press("Space");
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    await trigger.focus();
    expect(await trigger.evaluate(node => getComputedStyle(node).borderColor)).not.toBe(border);
    await capture(page, "section-tabs", theme);

    await page.goto("#buddies/router/routing");
    const routing = page.locator('[data-slot="router-detail"]');
    const bar = routing.locator('[data-slot="quant-bar-trigger"]').first();
    await expect(bar).toHaveAttribute("aria-expanded", "false");
    await capture(page, "routing-default", theme);
    await bar.click();
    await expect(bar).toHaveAttribute("aria-expanded", "true");
    await expect(routing.locator('[data-slot="quant-bar-detail"]').first()).toContainText("应答");
    await capture(page, "routing-expanded", theme);
    await page.reload();
    await expect(bar).toHaveAttribute("aria-expanded", "true");

    await page.goto("#buddies/router/maintenance");
    const maintenance = page.locator('[data-slot="maintenance-record"]');
    const maintenanceBar = maintenance.first().locator('[data-slot="quant-bar-trigger"]');
    await expect(maintenanceBar).toHaveAttribute("aria-expanded", "false");
    await maintenanceBar.click();
    await expect(maintenance.first().locator('[data-slot="quant-bar-detail"]')).toContainText("缓存输入");
    await expect(maintenance.nth(1)).not.toContainText("summary-citation-outside-batch");
    await expect(maintenance.nth(1).locator('[aria-description*="summary-citation-outside-batch"]')).toContainText("本批次之外");
    await capture(page, "maintenance", theme);

    await page.goto("#buddies/router/profiles");
    const profiles = page.locator('[data-slot="profile-records"]');
    const profileBars = profiles.locator('[data-slot="quant-bar-trigger"]');
    await expect(profileBars.first()).toHaveAttribute("aria-expanded", "false");
    for (const profileBar of await profileBars.all()) await profileBar.click();
    await expect(profiles.locator('[data-slot="quant-bar-detail"]').first()).toBeVisible();
    await capture(page, "profiles", theme);
    await expectNoSidewaysScroll(page);
  });

  test("places a queue explanation beside the queued task state", async ({ page }) => {
    await page.route("**/api/tasks/preview-run-b5", async route => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), state: "queued", queueReason: "cwd-overlap" } });
    });
    await open(page, "#tasks/macro-22222222-2222-4222-8222-222222222222/preview-run-b5");
    await expect(page.locator('[data-slot="detail-queue-reason"]')).toHaveText("该工作目录正被其他微任务使用");
    await capture(page, "queued-task", theme);
  });
});
