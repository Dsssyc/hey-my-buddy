import { expect } from "@playwright/test";
import { openLegend, test } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";

for (const { locale, theme, width, counter, next, waitMeaning, clusterMeaning, idleMeaning } of [
  { locale: "zh-CN", theme: "dark", width: 390, counter: "步骤", next: "下一步", waitMeaning: "等待 Host 或 Lead 决定或验收的时段。", clusterMeaning: "挨近的记号合成一个，数字是个数，点开看每一项。", idleMeaning: "较长的无活动时段在时间轴上折叠显示。" },
  { locale: "en-US", theme: "light", width: 1440, counter: "Step", next: "Next step", waitMeaning: "Time waiting for the Host or Lead to decide or accept the work.", clusterMeaning: "Nearby marks are merged; the number counts them. Click to list each.", idleMeaning: "A long inactive stretch folded on the timeline." },
]) test.describe(`steps and legend ${locale}`, () => {
  test.use({ locale, viewport: { width, height: 900 }, reducedMotion: "reduce" });

  test("shows position above reachable arrows and explains every legend mark", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await page.goto(`#tasks/${A}/preview-run-a1/1`);
    const strip = page.locator('[data-slot="detail-strip"]');
    const position = strip.locator('[data-slot="step-counter"]');
    const forward = strip.getByRole("button", { name: next });
    await expect(position).toHaveText(new RegExp(`^${counter} 1 / [2-9][0-9]*$`));
    const before = await position.textContent();
    const [positionBox, arrowBox] = await Promise.all([position.boundingBox(), forward.boundingBox()]);
    expect(positionBox!.y + positionBox!.height).toBeLessThan(arrowBox!.y);
    expect(arrowBox!.width).toBe(28);
    expect(arrowBox!.height).toBe(28);
    await expect(forward).toHaveAttribute("data-variant", "ghost");
    await expect(forward.locator("svg")).toHaveAttribute("stroke-width", "2");
    await forward.click();
    await expect(position).toHaveText(before!.replace(" 1 / ", " 2 / "));

    await page.goto(`#tasks/${A}`);
    const legend = await openLegend(page);
    const items = legend.locator('[data-slot="legend-item"]');
    await expect(items).toHaveCount(10);
    for (const item of await items.all()) {
      await expect(item).not.toHaveAttribute("tabindex", "0");
      await expect(item).toContainText(/。|\./);
    }
    for (const [kind, meaning] of [["waiting", waitMeaning], ["cluster", clusterMeaning], ["idle", idleMeaning]] as const) {
      const item = legend.locator(`[data-kind="${kind}"]`);
      await expect(item).toContainText(meaning);
    }
    await page.keyboard.press("Escape");
    await expect(page.locator('[data-slot="legend-trigger"]')).toBeFocused();
    await expect(legend).toHaveCount(0);
  });

  test("draws accepted and failed legend circles with the timeline glyph", async ({ page }) => {
    await page.goto(`#tasks/${A}`);
    await openLegend(page);
    const sameGlyph = async (kind: "accepted" | "failed", trackSlot: string) => {
      const legend = page.locator(`[data-slot="timeline-legend"] [data-kind="${kind}"] [data-slot="conclusion-glyph"]`);
      const track = page.locator(`[data-slot="${trackSlot}"] [data-slot="conclusion-glyph"]`).first();
      await expect(legend).toHaveCount(1);
      await expect(track).toBeVisible();
      for (const part of ["circle", "path"]) {
        const drawn = track.locator(part);
        const key = legend.locator(part);
        await expect(key).toHaveAttribute("class", await drawn.getAttribute("class") ?? "");
        if (part === "circle") await expect(key).toHaveAttribute("r", await drawn.getAttribute("r") ?? "");
        else await expect(key).toHaveAttribute("stroke-width", await drawn.getAttribute("stroke-width") ?? "");
      }
    };
    await sameGlyph("accepted", "accept-mark");
    await page.goto(`#tasks/${B}`);
    await openLegend(page);
    await sameGlyph("failed", "failed-conclusion-mark");
  });
});
