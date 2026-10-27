import { expect, type Locator, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, test } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const run = "preview-run-a1";
const url = `#tasks/${A}/${run}/7`;
const captures = fileURLToPath(new URL("../../../tmp/readability-r9", import.meta.url));
const header = (page: Page) => page.locator('[data-slot=detail-header]');
const refresh = (page: Page) => header(page).locator('[data-slot=detail-refresh]');
const icon = (page: Page) => refresh(page).locator('[data-slot=detail-refresh-icon]');
async function shot(page: Page, name: string) { await mkdir(captures, { recursive: true }); await page.screenshot({ path: `${captures}/${name}.png` }); }
async function ready(page: Page) { await open(page, url); await expect(page.locator('[data-slot=detail-pane]')).toBeVisible(); }
async function assertBack(page: Page, expected?: number) {
  const back = (await header(page).locator('[data-slot=detail-back] svg').boundingBox())!;
  const section = (await page.locator('[data-slot=task-detail]').boundingBox())!;
  expect(back.x - section.x).toBeCloseTo(12, 0);
  if (expected !== undefined) expect(Math.abs(back.x - expected)).toBeLessThanOrEqual(1);
  return back.x;
}

for (const theme of ["dark", "light"]) for (const width of [1440, 390]) test.describe(`detail head geometry ${theme} ${width}`, () => {
  test.use({ locale: "zh-CN", viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test("aligns the loaded, loading and failed back arrow with the step buttons", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    await page.route(`**/api/tasks/${run}`, async route => { await gate; await route.continue(); });
    await open(page, url);
    await expect(header(page).locator('[data-slot=detail-back]')).toBeVisible();
    const before = await assertBack(page);
    release();
    await expect(page.locator('[data-slot=step-navigation]')).toBeVisible();
    const step = (await page.locator('[data-slot=step-navigation] button').first().boundingBox())!;
    await assertBack(page, step.x);
    expect(Math.abs(before - step.x)).toBeLessThanOrEqual(1);
    await shot(page, `head-${theme}-${width}`);
    await page.route("**/api/tasks/no-such-run", route => route.fulfill({ json: { runId: "another-run" } }));
    await page.goto(`#tasks/${A}/no-such-run`);
    await expect(page.locator('[data-slot=task-detail] [role=alert]')).toBeVisible();
    await assertBack(page, before);
  });
});

async function observeCycles(svg: Locator) {
  await svg.evaluate(node => {
    node.setAttribute("data-cycles", "0");
    node.addEventListener("animationiteration", event => {
      node.setAttribute("data-cycles", String(Number(node.getAttribute("data-cycles")) + 1));
      node.setAttribute("data-last-turn", String((event as AnimationEvent).elapsedTime));
    });
  });
}
async function settled(page: Page, cycles: number) {
  await expect(icon(page)).not.toHaveClass(/detail-refresh-turn/);
  expect(Number(await icon(page).getAttribute("data-cycles"))).toBeGreaterThanOrEqual(cycles);
  const elapsed = Number(await icon(page).getAttribute("data-last-turn"));
  expect(elapsed).toBeGreaterThanOrEqual(1);
  expect(elapsed).toBe(Math.round(elapsed));
  await expect(icon(page)).toHaveCSS("transform", "none");
  await expect(refresh(page)).toBeEnabled();
}
async function changedReply(page: Page, title: string, gate?: Promise<void>) {
  await page.route(`**/api/tasks/${run}`, async route => {
    const response = await route.fetch();
    const value = await response.json();
    value.workflow.title = title;
    if (gate) await gate;
    await route.fulfill({ response, json: value });
  });
}

test.describe("refresh motion", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 1000 }, reducedMotion: "no-preference" });
  test("shows a full turn for a fast reply and stops after the new content is visible", async ({ page }) => {
    await ready(page);
    await observeCycles(icon(page));
    await changedReply(page, "刷新之后的新标题");
    await refresh(page).click();
    await expect(header(page).getByRole("heading")).toHaveText("刷新之后的新标题");
    await expect(refresh(page)).toHaveAttribute("aria-busy", "false");
    await expect(icon(page)).toHaveCSS("animation-name", "detail-refresh-turn");
    await expect(refresh(page)).toBeDisabled();
    const first = await icon(page).evaluate(node => getComputedStyle(node).transform);
    await page.waitForTimeout(120);
    expect(await icon(page).evaluate(node => getComputedStyle(node).transform)).not.toBe(first);
    await settled(page, 1);
  });

  test("keeps turning through a slow read and finishes at the next complete turn", async ({ page }) => {
    await ready(page);
    await observeCycles(icon(page));
    let release!: () => void;
    await changedReply(page, "慢读取的新标题", new Promise<void>(resolve => { release = resolve; }));
    await refresh(page).click();
    await expect.poll(async () => Number(await icon(page).getAttribute("data-cycles")), { timeout: 4000 }).toBeGreaterThanOrEqual(2);
    await expect(refresh(page)).toHaveAttribute("aria-busy", "true");
    await expect(icon(page)).toHaveCSS("animation-name", "detail-refresh-turn");
    release();
    await expect(header(page).getByRole("heading")).toHaveText("慢读取的新标题");
    await settled(page, 3);
  });

  test("stops after a failed read while keeping the displayed content", async ({ page }) => {
    await ready(page);
    const title = await header(page).getByRole("heading").textContent();
    await observeCycles(icon(page));
    await page.route(`**/api/tasks/${run}`, route => route.fulfill({ json: { runId: "wrong-run" } }));
    await refresh(page).click();
    await expect(header(page).getByRole("alert")).toBeVisible();
    await expect(header(page).getByRole("heading")).toHaveText(title!);
    await settled(page, 1);
  });
});

test.describe("refresh with reduced motion", () => {
  test.use({ locale: "zh-CN", reducedMotion: "reduce" });
  test("dims without rotating and disables the button only while the read is pending", async ({ page }) => {
    await ready(page);
    let release!: () => void;
    await changedReply(page, "减少动态效果后的内容", new Promise<void>(resolve => { release = resolve; }));
    await refresh(page).click();
    await expect(refresh(page)).toHaveAttribute("aria-busy", "true");
    await expect(refresh(page)).toBeDisabled();
    await expect(icon(page)).toHaveCSS("animation-name", "none");
    await expect(icon(page)).toHaveCSS("transform", "none");
    await expect(icon(page)).toHaveCSS("opacity", "0.5");
    release();
    await expect(header(page).getByRole("heading")).toHaveText("减少动态效果后的内容");
    await expect(refresh(page)).toBeEnabled();
    await expect(icon(page)).toHaveCSS("opacity", "1");
    await expect(icon(page)).toHaveCSS("animation-name", "none");
  });
});
