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

  test("keeps a dense outline aligned, expandable and clear of its popover", async ({ page }) => {
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
    await expectTextUncut(tree.locator(".domain-tree-name"));
    await capture(page, "domains-collapsed", theme);
    const first = rows.first();
    await first.click();
    await expect(rows).toHaveCount(47);
    await tree.locator(".domain-tree-overview p").click();
    await page.mouse.move(0, 0);
    await expect(tree.locator(".domain-tree-popover")).toBeHidden();
    await capture(page, "domains-one-expanded", theme);
    await rows.nth(1).hover();
    await expect(first.locator('[data-segment="preview-outline-01-1"]')).toHaveAttribute("data-highlight", "true");
    const panel = tree.locator(".domain-tree-popover");
    await expect(panel).toBeVisible();
    const overlaps = await tree.evaluate(node => {
      const panel = node.querySelector(".domain-tree-popover")!.getBoundingClientRect();
      return [...node.querySelectorAll(".domain-tree-name")].filter(label => {
        const box = label.getBoundingClientRect();
        return box.left < panel.right && box.right > panel.left && box.top < panel.bottom && box.bottom > panel.top;
      }).length;
    });
    expect(overlaps).toBe(0);
    await capture(page, "domains-hover", theme);
    await rows.nth(1).press("Enter");
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
