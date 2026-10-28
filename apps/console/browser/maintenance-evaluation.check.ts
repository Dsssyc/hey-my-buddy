import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect } from "@playwright/test";
import { open, test, expectNoSidewaysScroll, expectTextUncut } from "./preview";

const matrixCaptures = fileURLToPath(new URL("../../../tmp/maintenance-polish", import.meta.url));

for (const theme of ["dark", "light"]) for (const width of [1440, 390]) {
  test.describe(`maintenance and evaluation ${theme} ${width}px`, () => {
    test.use({ locale: "zh-CN", viewport: { width, height: 1200 }, reducedMotion: "reduce" });
    test("shows maintenance history and domain hierarchy and opens the eligible buddy picker", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, "#buddies/roles");
      const plane = page.locator('[data-slot="maintenance-plane"]');
      await expect(plane).toContainText("待处理 3 个微任务");
      await expect(plane).toContainText("完成");
      await expect(plane).toContainText("界面工程");
      await expect(plane).toContainText("路由判断");
      await plane.evaluate(node => node.scrollIntoView({ block: "start" }));
      await expectNoSidewaysScroll(page);
      await expectTextUncut(plane.locator("[data-slot=domain-row] p"));
      const action = plane.getByRole("group", { name: "立即维护", exact: true });
      const actionLabel = action.locator(":scope > div").first().locator("span").first();
      expect(await actionLabel.evaluate(node => node.getClientRects().length)).toBe(1);
      await expectTextUncut(action.locator('[data-slot="maintenance-pending"]'));
      const destination = process.env.HMB_ROUTE_SCREENSHOTS;
      if (destination) { await mkdir(destination, { recursive: true }); await page.screenshot({ path: join(destination, `maintenance-${theme}-${width}.png`), fullPage: true }); }
      await plane.getByRole("button", { name: "选择 buddy" }).click();
      const picker = page.getByRole("dialog");
      await expect(picker).toBeVisible();
      await expect(picker.getByRole("button", { name: /dsh \/ deepseek-official \/ deepseek-flash \/ max/ })).toBeVisible();
      await expectNoSidewaysScroll(page);
      await page.keyboard.press("Escape");
      await plane.getByRole("button", { name: "改名" }).first().click();
      await expect(page.getByRole("dialog").getByLabel("名称")).toBeVisible();
      await expect(page.getByRole("dialog").getByLabel("定义")).toBeVisible();
      await page.keyboard.press("Escape");
    });
    test("shows summary beside counts and a scrollable collapsible task matrix", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, "#buddies/catalog/buddy/dsh%3Adeepseek-official%3Adeepseek-flash%3Amax");
      const evaluation = page.locator('[data-slot="kind-evaluation"]');
      await evaluation.getByRole("button", { name: /评价与领域经验/ }).click();
      await expect(evaluation).toContainText("合成样例：界面改造有过一次返工");
      await expect(evaluation.getByRole("table").getByRole("columnheader")).toHaveCount(8);
      await evaluation.evaluate(node => node.scrollIntoView({ block: "start" }));
      await expectNoSidewaysScroll(page);
      const cells = evaluation.getByRole("table").getByLabel("无样本");
      expect(await cells.count()).toBeGreaterThan(0);
      for (const cell of await cells.all()) await expect(cell).toBeEmpty();
      const destination = process.env.HMB_ROUTE_SCREENSHOTS;
      if (destination) { await mkdir(destination, { recursive: true }); await page.screenshot({ path: join(destination, `evaluation-${theme}-${width}.png`), fullPage: true }); }
      const scroll = evaluation.locator('[data-slot="evaluation-table-scroll"]');
      await scroll.evaluate(node => { node.scrollLeft = node.scrollWidth; });
      await expect(evaluation.getByRole("columnheader", { name: "撰写" })).toBeInViewport();
      await scroll.evaluate(node => { node.scrollLeft = 0; });
      await evaluation.getByRole("button", { name: "界面工程", exact: true }).click();
      await expect(evaluation.getByRole("rowheader", { name: /路由判断/ })).toHaveCount(0);
      await evaluation.getByRole("link", { name: "合成预览任务 A1" }).click();
      await expect(page).toHaveURL(/#tasks\/.*\/preview-run-a1$/);
    });
    test("lists only the sampled domains of a sparse buddy and their ancestors", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, "#buddies/catalog/buddy/codex%3Aopenai%3Agpt-5-codex%3Ahigh");
      const evaluation = page.locator('[data-slot="kind-evaluation"]');
      await evaluation.getByRole("button", { name: /评价与领域经验/ }).click();
      const matrix = evaluation.locator('section[aria-label="领域与任务类型"]');
      await expect(matrix.getByRole("rowheader")).toHaveCount(3);
      await expect(matrix.getByRole("rowheader", { name: /界面工程/ })).toBeVisible();
      await expect(matrix.getByRole("rowheader", { name: /控制台/ })).toBeVisible();
      await expect(matrix.getByRole("rowheader", { name: /样式与排版/ })).toBeVisible();
      await expect(matrix.getByRole("rowheader", { name: /路由判断|文档与技能|共享技能/ })).toHaveCount(0);
      await expect(matrix.getByLabel("无样本")).toHaveCount(20);
      await expectNoSidewaysScroll(page);
      await mkdir(matrixCaptures, { recursive: true });
      await matrix.screenshot({ path: join(matrixCaptures, `domain-matrix-${theme}-${width}.png`) });
    });
  });
}
