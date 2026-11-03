import { expect } from "@playwright/test";
import { open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";

for (const { locale, labels } of [
  { locale: "zh-CN", labels: ["从开始到最近活动", "有工作在运行的时间", "各回合相加"] },
  { locale: "en-US", labels: ["Start to last activity", "Time with work running", "Turns added up"] },
]) test.describe(`round 15 facts in ${locale}`, () => {
  test.use({ locale, viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });

  test("keeps the verified date in the hint and the clock alone in the narrow bar", async ({ page }) => {
    await open(page, "#tasks");
    const bar = page.getByRole("banner").first();
    const visible = bar.locator("time:visible");
    await expect(visible).toHaveCount(1);
    await expect(visible).toHaveText(/^\d{2}:\d{2}:\d{2}$/);
    await page.keyboard.press("Tab");
    await visible.locator("..").focus();
    await expect(page.getByRole("tooltip")).toContainText(/\d{4}/);
    await page.setViewportSize({ width: 700, height: 844 });
    await expect(bar.locator("time:visible")).toHaveCount(1);
  });

  test("aligns three self-explanatory labels without tooltip-only meanings", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    await page.locator('[data-slot=macro-overview-header]').getByRole("button", { name: locale === "zh-CN" ? "时间统计" : "Time statistics" }).click();
    const stats = page.locator('[data-slot=time-stats-values]');
    await expect(stats.locator("dt")).toHaveText(labels);
    const values = await stats.locator("dd").evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().left));
    expect(values).toHaveLength(3);
    expect(new Set(values.map(value => Math.round(value))).size).toBe(1);
    await stats.locator("dt span").first().hover();
    await page.waitForTimeout(700);
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    const copy = page.locator('[data-slot=macro-overview-header] button[aria-label*="macro-"]');
    await expect(copy).not.toHaveCSS("border-bottom-style", "dotted");
    await expect(copy.locator("svg.lucide-copy")).toHaveCount(1);
  });
});

test("the expanded model uses the selected row's ink edge and a still availability mark", async ({ page }) => {
  await open(page, "#buddies");
  const row = page.locator('[data-slot=model-row]').first();
  await row.locator("button").first().click();
  const drawer = page.locator('[data-slot=model-drawer]');
  await expect(drawer).toBeVisible();
  await expect(drawer).toHaveCSS("border-left-color", await row.evaluate(node => getComputedStyle(node).color));
  await expect(drawer.locator('[data-slot=state-mark]').first()).toHaveCSS("animation-name", "none");
});
