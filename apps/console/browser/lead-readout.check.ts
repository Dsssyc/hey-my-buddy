import { expect, type Locator } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, settle, test } from "./preview";

const macro = "macro-22222222-2222-4222-8222-222222222222";
const leadId = "preview-run-b3";
const captures = fileURLToPath(new URL("../../../tmp/console-hints/lead", import.meta.url));

async function visibleText(summary: Locator) {
  return summary.evaluate(element => {
    const frame = element.closest('[role="status"]')!.getBoundingClientRect();
    const bar = element.closest('[data-slot="timeline-status"]')!.getBoundingClientRect();
    const range = document.createRange(); range.selectNodeContents(element);
    const rects = [...range.getClientRects()].filter(rect => rect.width > 0);
    return { text: element.textContent, lines: rects.length, whole: rects.every(rect =>
      rect.left >= frame.left - 1 && rect.right <= frame.right + 1 && rect.top >= bar.top && rect.bottom <= bar.bottom),
      frame: { left: frame.left, right: frame.right, top: frame.top, bottom: frame.bottom },
      bar: { left: bar.left, right: bar.right, top: bar.top, bottom: bar.bottom, height: bar.height } };
  });
}

for (const language of ["zh", "en"]) test(`Lead totals precede its row facts and remain visible at 1280px in ${language}`, async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 850 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(language => {
    localStorage.setItem("hey-my-buddy.console.theme", "dark");
    localStorage.setItem("hey-my-buddy.language", language);
  }, language);
  await open(page, `#tasks/${macro}`);
  const lead = page.locator(`[data-slot="timeline-row-name"][data-run-id="${leadId}"]`);
  const status = page.locator('[data-slot="timeline-status"] [role="status"]');
  const summary = status.locator('[data-slot="timeline-lane-summary"]');
  const baseline = (await page.locator('[data-slot="timeline-status"]').boundingBox())!;
  const check = async () => {
    await expect(summary).toContainText(language === "zh" ? "5 个微任务" : "5 micro tasks");
    await expect(summary).toContainText(language === "zh" ? "输入 26.6k（含缓存 12.4k）" : "Input 26.6k");
    await expect(summary).toContainText(language === "zh" ? "输出 2.7k" : "Output 2.7k");
    const facts = await visibleText(summary);
    expect(facts.whole, JSON.stringify(facts)).toBe(true);
    expect(facts.lines).toBe(1);
    expect(facts.bar.height).toBe(baseline.height);
    expect(facts.bar.top).toBe(baseline.y);
    await expect(status).toHaveCSS("white-space", "nowrap");
    await expect(status.locator('[data-slot="timeline-row-readout"]')).toHaveCSS("text-overflow", "ellipsis");
    expect((await status.innerText()).startsWith(facts.text!)).toBe(true);
    await expect(status).toContainText(language === "zh" ? "回合" : "turns");
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    return facts;
  };

  await lead.hover();
  const facts = await check();
  await mkdir(captures, { recursive: true });
  await writeFile(`${captures}/${language}-lead-readout.json`, JSON.stringify(facts, null, 2) + "\n");
  await page.screenshot({ path: `${captures}/${language}-dark-1280-lead-readout.png` });
  await page.mouse.move(0, 0);
  await page.keyboard.press("Tab"); await lead.focus();
  await check();
  await lead.blur();
  const track = page.locator(`[data-slot="timeline-lane-lead"][data-run-id="${leadId}"] [data-slot="timeline-track"]`);
  const box = (await track.boundingBox())!;
  await track.click({ position: { x: box.width - 5, y: 2 } });
  await page.mouse.move(0, 0); await settle(page);
  await expect(page.locator('[data-slot="task-detail"]')).toHaveCount(0);
  await check();
  const member = page.locator('[data-slot="timeline-row-name"][data-run-id="preview-run-b4"]');
  await member.hover();
  await expect(summary).toHaveCount(0);
  await lead.click();
  await expect(page.locator('[data-slot="task-detail"]')).toBeVisible();
  await expect(status).toBeEmpty();
  await expect(page.locator('[data-slot="timeline-status"] [data-slot="legend-trigger"]')).toBeVisible();
});
