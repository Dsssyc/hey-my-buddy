import { expect } from "@playwright/test";
import { test, open, settle } from "./preview";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const shots = fileURLToPath(new URL("../../../tmp/readability", import.meta.url));

for (const theme of ["dark", "light"]) for (const width of [1440, 390]) test.describe(`readability ${theme} ${width}`, () => {
  test.use({ viewport: { width, height: 1000 }, locale: "zh-CN", reducedMotion: "reduce" });
  test("keeps the pointer in the whole viewport and records review screenshots", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, `#tasks/${macro}`);
    const viewport = page.locator('[data-slot=timeline-tracks]');
    await expect(viewport).toBeVisible();
    const box = (await viewport.boundingBox())!;
    const axis = (await page.locator('[data-slot=timeline-axis]').boundingBox())!;
    const pointer = page.locator('[data-slot=timeline-pointer]');
    const x = Math.min(box.x + box.width - 20, axis.x + 60);
    for (const y of [box.y + 38, box.y + 90, box.y + box.height - 20]) {
      await page.mouse.move(x, y);
      await expect(pointer).toHaveCount(1);
      expect((await pointer.boundingBox())!.height).toBeCloseTo(box.height, 0);
    }
    await page.mouse.move(x + 25, box.y + box.height - 20);
    expect((await pointer.boundingBox())!.x).toBeCloseTo(x + 25, 0);
    for (const [exitX, exitY] of [[x, box.y - 1], [x, box.y + box.height + 1], [box.x - 1, box.y + 50], [box.x + box.width + 1, box.y + 50]]) {
      await page.mouse.move(x, box.y + 70);
      await page.mouse.move(exitX!, exitY!);
      await expect(pointer).toHaveCount(0);
      await expect(page.locator('[data-slot=pointer-clock]')).toHaveCount(0);
    }
    await mkdir(shots, { recursive: true });
    await page.mouse.move(0, 0);
    await page.screenshot({ path: `${shots}/timeline-${theme}-${width}.png` });
    await page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a1-h1"]').click();
    await settle(page);
    await page.screenshot({ path: `${shots}/detail-${theme}-${width}.png` });
  });
});

test.describe("name width", () => {
  test.use({ viewport: { width: 1440, height: 1000 }, locale: "zh-CN", reducedMotion: "reduce" });
  test("drags, constrains, persists and resets with keyboard access", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    const divider = page.locator('[data-slot=timeline-name-divider]');
    await expect(divider).toHaveAttribute("aria-valuenow", "272");
    const box = (await divider.boundingBox())!;
    await page.mouse.move(box.x + 3, box.y + 50); await page.mouse.down();
    await page.mouse.move(box.x + 73, box.y + 50); await page.mouse.up();
    await expect(divider).toHaveAttribute("aria-valuenow", "342");
    await page.reload(); await expect(divider).toHaveAttribute("aria-valuenow", "342");
    await divider.focus(); await page.keyboard.press("End"); await expect(divider).toHaveAttribute("aria-valuenow", "440");
    await page.keyboard.press("ArrowRight"); await expect(divider).toHaveAttribute("aria-valuenow", "440");
    await page.keyboard.press("Home"); await expect(divider).toHaveAttribute("aria-valuenow", "200");
    await divider.dblclick(); await expect(divider).toHaveAttribute("aria-valuenow", "272");
    await page.locator('[data-slot=timeline-row-name][data-run-id="preview-run-a1-h1"]').click();
    await expect(divider).toHaveAttribute("aria-valuenow", "272");
  });
});

for (const reducedMotion of ["reduce", "no-preference"] as const) test.describe(`execution motion ${reducedMotion}`, () => {
  test.use({ viewport: { width: 1440, height: 1000 }, reducedMotion });
  test("keeps the buddy hue, breathes only at the right edge and survives polling", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    const bar = page.locator('[data-slot=execution-bar][data-outcome=running]').first();
    await expect(bar).toBeVisible();
    const group = bar.locator('..');
    const spanId = (await group.getAttribute("data-key"))!.slice(5);
    const tail = page.locator(`[data-slot=execution-breath][data-span-id="${spanId}"]`);
    await expect(tail).toBeVisible();
    const geometry = await bar.locator("xpath=ancestor::*[@data-slot='timeline-track']").evaluate(node => {
      const base = node.querySelector<SVGRectElement>('[data-slot=execution-bar][data-outcome=running]')!;
      const end = node.querySelector<SVGRectElement>('[data-slot=execution-breath]')!;
      return { width: end.width.baseVal.value, right: end.x.baseVal.value + end.width.baseVal.value, baseRight: base.x.baseVal.value + base.width.baseVal.value, animations: node.getAnimations({ subtree: true }).length, animation: getComputedStyle(base).animationName, tailAnimation: getComputedStyle(end).animationName, fill: getComputedStyle(base).fill };
    });
    expect(geometry.width).toBe(16); expect(geometry.right).toBeCloseTo(geometry.baseRight, 1);
    expect(geometry.animations).toBe(reducedMotion === "reduce" ? 0 : 1);
    expect(geometry.animation).toBe("none"); expect(geometry.tailAnimation).toBe(reducedMotion === "reduce" ? "none" : "execution-breathe");
    const edge = (await tail.boundingBox())!;
    const sample = async () => {
      const data = (await page.screenshot()).toString("base64");
      return page.evaluate(async ({ data, x, y }) => {
        const image = new Image(); image.src = `data:image/png;base64,${data}`; await image.decode();
        const canvas = document.createElement("canvas"); canvas.width = image.width; canvas.height = image.height;
        const context = canvas.getContext("2d")!; context.drawImage(image, 0, 0);
        const pixel = context.getImageData(Math.floor(x), Math.floor(y), 1, 1).data;
        return pixel[0]! * .2126 + pixel[1]! * .7152 + pixel[2]! * .0722;
      }, { data, x: edge.x + edge.width - 2, y: edge.y + edge.height - 3 });
    };
    if (reducedMotion === "no-preference") {
      await tail.evaluate(node => { const animation = node.getAnimations()[0]!; animation.pause(); animation.currentTime = 0; });
      const dim = await sample();
      await tail.evaluate(node => node.getAnimations()[0]!.currentTime = 1000);
      expect(await sample()).toBeGreaterThan(dim + 15);
      await tail.evaluate(async node => { const animation = node.getAnimations()[0]!; animation.play(); await animation.ready; });
    } else {
      await expect(tail).toHaveCSS("opacity", "1");
      const still = await sample(); await page.waitForTimeout(150);
      expect(await sample()).toBe(still);
    }
    const label = await group.getAttribute("aria-label");
    const all = page.locator('[data-slot=execution-bar]');
    await expect(all.first()).toBeVisible();
    expect(label).toBeTruthy();
    if (reducedMotion === "no-preference") {
      const start = await tail.evaluate(node => node.getAnimations()[0]!.startTime);
      await page.waitForResponse(response => response.url().includes(`/api/macro-tasks/${macro}/timeline`) && response.ok());
      await expect(bar).toHaveCSS("fill", geometry.fill);
      expect(await tail.evaluate(node => node.getAnimations()[0]!.startTime)).toBe(start);
    }
    await group.hover();
    await expect(page.getByRole("tooltip")).toContainText("/");
  });
});


test.describe("Lead lane gaps", () => {
  test.use({ viewport: { width: 1440, height: 1000 }, locale: "zh-CN", reducedMotion: "reduce" });
  test("keeps the pointer moving between lanes and puts the role in the identity line", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
    await open(page, "#tasks/macro-22222222-2222-4222-8222-222222222222");
    const lanes = page.locator('[data-slot=timeline-lane]');
    await expect(lanes).toHaveCount(3);
    const name = lanes.first().locator('[data-slot=timeline-row-name]').first();
    await expect(name.locator(':scope > span').last()).toContainText("Lead ·");
    await expect(lanes.first().locator('[data-slot=lane-guide]')).not.toHaveCount(0);
    const second = (await lanes.nth(1).boundingBox())!;
    const axis = (await page.locator('[data-slot=timeline-axis]').boundingBox())!;
    await page.mouse.move(axis.x + 70, second.y - 12);
    await expect(page.locator('[data-slot=timeline-pointer]')).toHaveCount(1);
    await mkdir(shots, { recursive: true });
    await page.screenshot({ path: `${shots}/lead-dark-1440.png` });
  });
});

test.describe("storage-independent timeline", () => {
  test.use({ viewport: { width: 1440, height: 1000 }, locale: "zh-CN", reducedMotion: "reduce" });
  test("keeps the same colours and default width when site storage access throws", async ({ page }) => {
    await open(page, `#tasks/${macro}`);
    const bars = page.locator('[data-slot=execution-bar]');
    await expect(bars.first()).toBeVisible();
    const colors = () => bars.evaluateAll(nodes => Object.fromEntries(nodes.map(node => [node.parentElement!.getAttribute("data-key")!, getComputedStyle(node).fill])));
    const expected = await colors();
    const divider = page.locator('[data-slot=timeline-name-divider]');
    await divider.focus(); await page.keyboard.press("End");
    await expect(divider).toHaveAttribute("aria-valuenow", "440");
    await page.addInitScript(() => Object.defineProperty(window, "localStorage", {
      configurable: true, get() { throw new DOMException("The operation is insecure.", "SecurityError"); },
    }));
    await page.reload();
    await expect(page.locator('[data-slot=macro-overview-header]')).toBeVisible();
    await expect(divider).toHaveAttribute("aria-valuenow", "272");
    await expect(bars).toHaveCount(Object.keys(expected).length);
    expect(await colors()).toEqual(expected);
    await page.goto(`#tasks/${macro}/preview-run-a1-h1`);
    await expect(page.locator('[data-slot=detail-strip]')).toBeVisible();
    for (const [key, color] of Object.entries(await colors())) expect(color, key).toBe(expected[key]);
    // Direct navigation in a fresh document must derive the same full-macro palette.
    await page.reload();
    await expect(page.locator('[data-slot=detail-strip]')).toBeVisible();
    await expect(bars.first()).toBeVisible();
    for (const [key, color] of Object.entries(await colors())) expect(color, key).toBe(expected[key]);
  });
});
