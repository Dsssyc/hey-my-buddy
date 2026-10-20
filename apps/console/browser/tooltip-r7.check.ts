import { expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, openLegend, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const captures = fileURLToPath(new URL("../../../tmp/readability-r7", import.meta.url));
async function capture(page: Page, name: string) {
  await mkdir(captures, { recursive: true });
  await page.screenshot({ path: `${captures}/${name}.png` });
}

for (const theme of ["dark", "light"]) test.describe(`tooltip policy ${theme}`, () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });

  test("delays every hover, allows clicks underneath and keeps keyboard and copy feedback immediate", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await page.clock.install({ time: new Date("2026-10-19T12:00:00Z") });
    await open(page, `#tasks/${macro}`);
    await expect(page.locator('[data-slot=task-work]')).toBeVisible();
    await page.clock.pauseAt(new Date("2026-10-19T12:01:00Z"));
    const tooltip = page.locator('[data-slot=tooltip-content]');
    const id = page.locator('[data-slot=macro-overview-header]').getByRole("button", { name: `复制标识 ${macro}` });
    await id.hover();
    await page.clock.runFor(500);
    await expect(tooltip).toHaveCount(0);
    await page.clock.runFor(100);
    await expect(tooltip).toBeVisible();
    expect(await tooltip.evaluate(node => getComputedStyle(node).pointerEvents)).toBe("none");
    expect(await tooltip.evaluate(node => getComputedStyle(node).textAlign)).toBe("center");

    // The visible tooltip cannot intercept a click at its own coordinates.
    const rect = (await tooltip.boundingBox())!;
    const point = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    await page.evaluate(({ x, y }) => {
      const target = document.createElement("button");
      target.dataset.slot = "under-tooltip";
      target.textContent = "Underneath";
      target.style.cssText = `position:fixed;left:${x - 10}px;top:${y - 10}px;width:20px;height:20px;z-index:40`;
      target.onclick = () => { target.dataset.clicked = "true"; };
      document.body.append(target);
    }, point);
    await expect(tooltip).toBeVisible();
    const hit = await page.evaluate(({ x, y }) => { const node = document.elementFromPoint(x, y); return { slot: node?.getAttribute("data-slot"), html: node?.outerHTML.slice(0, 600) }; }, point);
    expect(hit.slot, JSON.stringify(hit)).toBe("under-tooltip");
    await page.mouse.click(point.x, point.y);
    await expect(page.locator('[data-slot=under-tooltip]')).toHaveAttribute("data-clicked", "true");
    await page.locator('[data-slot=under-tooltip]').evaluate(node => node.remove());
    await expect(tooltip).toHaveCount(0);

    // Opening the next tooltip also takes the full 600 ms (no skip-delay window).
    const title = page.locator('[data-slot=macro-overview-header] h2');
    await title.hover();
    await page.clock.runFor(500);
    await expect(tooltip).toHaveCount(0);
    await page.clock.runFor(100);
    await expect(tooltip).toBeVisible();
    await capture(page, `title-tooltip-${theme}`);
    await page.mouse.move(0, 0);
    await expect(tooltip).toHaveCount(0);
    await id.focus();
    await expect(tooltip).toBeVisible(); // Clock has not advanced.
    await page.keyboard.press("Escape");
    await id.click();
    await expect(page.getByRole("status", { name: "" }).filter({ hasText: "已复制" })).toBeVisible();
    await expect(tooltip).toHaveText("已复制");

    // The original pointer route from the ID through the top bar stays usable.
    await page.clock.runFor(2100);
    await page.mouse.move(0, 0);
    await id.hover();
    await page.clock.runFor(600);
    await expect(tooltip).toBeVisible();
    const settings = page.getByRole("banner").first().getByRole("navigation").getByRole("link").last();
    const target = (await settings.boundingBox())!;
    await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 12 });
    await settings.click();
    await expect(page.locator('[data-slot=task-work]')).toBeHidden();
    await expect(settings).toHaveAttribute("aria-current", "page");
    await expect(tooltip).toHaveCount(0);
  });

  test("shares upright cluster drawings and quiet idle stripes in both views", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, `#tasks/${macro}`);
    const legend = await openLegend(page);
    await expect(legend.locator("i")).toHaveCount(0);
    const sample = legend.locator('[data-kind=cluster] [data-slot=cluster-mark]');
    const actual = page.locator('[data-slot=event-cluster] [data-slot=cluster-mark]').first();
    await expect(actual).toBeVisible();
    const drawing = async (node: typeof sample) => node.evaluate(node => ({
      radius: node.querySelector("circle")!.getAttribute("r"),
      stroke: getComputedStyle(node.querySelector("circle")!).stroke,
      fontStyle: getComputedStyle(node.querySelector("text")!).fontStyle,
      weight: getComputedStyle(node.querySelector("text")!).fontWeight,
    }));
    expect(await drawing(sample)).toEqual(await drawing(actual));
    expect((await drawing(sample)).fontStyle).toBe("normal");
    const line = await page.locator("html").evaluate(node => getComputedStyle(node).getPropertyValue("--line").trim());
    expect(line).toBe(theme === "dark" ? "#26282c" : "#e4e2d9");
    expect(await page.locator("html").evaluate(node => getComputedStyle(node).getPropertyValue("--idle-stripe"))).toBe("");
    const stripe = page.locator('pattern path[stroke="var(--line)"]');
    expect(await stripe.count()).toBeGreaterThan(1);
    const stripeColor = await stripe.first().evaluate(node => getComputedStyle(node).stroke);
    for (const mark of await page.locator('[data-slot=axis-break-mark]').all()) expect(await mark.evaluate(node => getComputedStyle(node).stroke)).toBe(stripeColor);
    expect(await legend.locator('[data-kind=idle] [aria-hidden=true]').evaluate(node => getComputedStyle(node).backgroundImage)).toContain(stripeColor);
    await capture(page, `timeline-${theme}`);
    await page.keyboard.press("Escape");
    await page.locator('[data-slot=macro-overview-header] h2').hover();
    const multiline = page.locator('[data-slot=tooltip-content]');
    await expect(multiline).toBeVisible();
    expect(await multiline.evaluate(node => getComputedStyle(node).textAlign)).toBe("center");
    expect(await multiline.evaluate(node => {
      const text = [...node.childNodes].find(child => child.nodeType === Node.TEXT_NODE)!;
      const range = document.createRange(); range.selectNodeContents(text);
      return range.getClientRects().length;
    })).toBeGreaterThan(1);
    await capture(page, `multiline-tooltip-${theme}`);
    await page.mouse.move(0, 0);
    await expect(multiline).toHaveCount(0);
    await page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a1"]').click();
    await expect(page.locator('[data-slot=step-navigation]')).toBeVisible();
    await capture(page, `detail-${theme}`);
  });
});
