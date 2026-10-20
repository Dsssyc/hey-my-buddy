import { expect, test, type Page } from "@playwright/test";
import { open } from "./preview";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const MACRO = "macro-22222222-2222-4222-8222-222222222222";
const LEAD = "preview-run-b3";
const MEMBER = "preview-run-b4";

const names = (page: Page) => page.locator('[data-slot=timeline-row-name]:visible');
const runIds = async (page: Page) => names(page).evaluateAll(nodes => nodes.map(node => node.getAttribute("data-run-id")));
const toggle = (page: Page) => page.locator(`[data-slot=lane-toggle][aria-controls="lane-${LEAD}"]:visible`);

test.describe("Lead groups across timeline and detail", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });

  test("keeps group order, collapse, keyboard movement, and state edges aligned", async ({ page }) => {
    await open(page, `#tasks/${MACRO}`);
    await expect(page.locator(`[data-slot=timeline-lane-lead][data-run-id="${LEAD}"]`)).toBeVisible();
    const timelineOrder = await runIds(page);
    const leadEdge = page.locator(`[data-slot=timeline-lane-lead] [data-run-id="${LEAD}"] [data-slot=row-state-edge]`).first();
    const leadGroup = page.locator(`[data-slot=timeline-lane-lead][data-run-id="${LEAD}"]`);
    const [edgeBox, groupBox, toggleBox] = await Promise.all([leadEdge.boundingBox(), leadGroup.boundingBox(), toggle(page).boundingBox()]);
    expect(edgeBox!.x).toBeCloseTo(groupBox!.x, 0);
    expect(toggleBox!.x).toBeGreaterThanOrEqual(edgeBox!.x);
    const member = page.locator(`[data-slot=timeline-row][data-run-id="${MEMBER}"]`);
    const [memberEdge, guide] = await Promise.all([
      member.locator('[data-slot=row-state-edge]').boundingBox(), member.locator('[data-slot=lane-guide]').boundingBox(),
    ]);
    expect(memberEdge!.x + memberEdge!.width / 2).toBeCloseTo(guide!.x + guide!.width / 2, 1);

    await toggle(page).click();
    await expect(toggle(page)).toHaveAttribute("aria-expanded", "false");
    const collapsedOrder = await runIds(page);
    expect(collapsedOrder).not.toContain(MEMBER);
    await page.locator(`[data-slot=timeline-lane-lead] [data-key="row:${LEAD}"]`).click();
    await expect(page.locator('[data-slot=task-detail]')).toBeVisible();
    await expect(page.locator('[data-slot=task-names]')).toBeVisible();
    expect(await runIds(page)).toEqual(collapsedOrder);
    await expect(toggle(page)).toHaveAttribute("aria-expanded", "false");
    await toggle(page).click();
    expect(await runIds(page)).toEqual(timelineOrder);
    const detailLead = page.locator(`[data-slot=detail-lane-lead][data-run-id="${LEAD}"]`);
    const [detailEdge, detailGroup] = await Promise.all([
      detailLead.locator('[data-slot=row-state-edge]').boundingBox(), detailLead.boundingBox(),
    ]);
    expect(detailEdge!.x).toBeCloseTo(detailGroup!.x, 0);
    const detailMember = page.locator(`[data-slot=task-names] [data-run-id="${MEMBER}"]`);
    const [detailMemberEdge, detailGuide] = await Promise.all([
      detailMember.locator('[data-slot=row-state-edge]').boundingBox(), detailMember.locator('[data-slot=lane-guide]').boundingBox(),
    ]);
    expect(detailMemberEdge!.x + detailMemberEdge!.width / 2).toBeCloseTo(detailGuide!.x + detailGuide!.width / 2, 1);
    const captures = fileURLToPath(new URL("../../../tmp/readability-r4", import.meta.url));
    await mkdir(captures, { recursive: true });
    await page.screenshot({ path: `${captures}/lead-detail-1440.png` });
    await page.locator(`[data-slot=task-names] [data-key="row:${LEAD}"]`).focus();
    await page.keyboard.press("ArrowDown");
    await expect(detailMember).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.locator('[data-slot=task-detail]')).toHaveCount(0);
    await expect(toggle(page)).toHaveAttribute("aria-expanded", "true");
  });
});

test("the detail list stays hidden at narrow width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page, `#tasks/${MACRO}/${LEAD}`);
  await expect(page.locator('[data-slot=task-detail]')).toBeVisible();
  await expect(page.locator('[data-slot=task-names]')).toBeHidden();
});
