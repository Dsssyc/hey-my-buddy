import { expect, type Page } from "@playwright/test";
import { open, settle, test } from "./preview";

const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";
const C = "macro-33333333-3333-4333-8333-333333333333";
const standalone = "run:preview-run-c1";
const expected = {
  "activity-desc": [B, A, standalone, C],
  "activity-asc": [C, standalone, A, B],
  "created-desc": [A, standalone, B, C],
  "created-asc": [C, B, standalone, A],
} as const;
type Mode = keyof typeof expected;
type Summary = { macroTaskId: string; createdAt: string | null; lastActivityAt: string | null };

function alphaGroup(page: Page) {
  return page.locator('[data-slot=macro-list] [data-slot=macro-group]')
    .filter({ has: page.locator(`[data-macro-task-id="${A}"]`) });
}

async function rows(page: Page) {
  return alphaGroup(page).locator("[data-macro-task-id]")
    .evaluateAll(nodes => nodes.map(node => node.getAttribute("data-macro-task-id")));
}

/** The time each row shows: its stamp and the screen-reader name telling which time it is. */
async function times(page: Page) {
  return alphaGroup(page).locator("[data-macro-task-id]").evaluateAll(nodes => nodes.map(node => {
    const time = node.querySelector("time");
    return [node.getAttribute("data-macro-task-id"), time?.getAttribute("datetime") ?? null,
            time?.querySelector(".sr-only")?.textContent ?? null];
  }));
}

for (const [locale, sortLabel, collapseLabel, basisLabels, directionLabels] of [
  ["zh-CN", "排序", "全部折叠", ["最近更新", "创建时间"], ["新的在前", "旧的在前"]],
  ["en-US", "Sort", "Collapse all", ["Last updated", "Created"], ["Newest first", "Oldest first"]],
] as const) for (const theme of ["light", "dark"] as const) {
  test.describe(`macro sort ${locale} ${theme}`, () => {
    test.use({ locale, viewport: { width: 1280, height: 800 }, reducedMotion: "reduce" });

    test("persists both choices, keeps the popover open and shows each row's requested time", async ({ page }, testInfo) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, "#tasks");
      const trigger = page.locator("[data-slot=macro-sort-button]");
      const popup = page.locator("[data-slot=macro-sort-popover]");
      const collapse = page.getByRole("button", { name: collapseLabel, exact: true });
      const list = page.locator("[data-slot=macro-list]");
      const currentLabel = (created: boolean, oldest: boolean) => `${sortLabel}${locale === "zh-CN" ? "：" : ": "}${basisLabels[created ? 1 : 0]} · ${directionLabels[oldest ? 1 : 0]}`;
      await expect(trigger).toHaveAccessibleName(currentLabel(false, false));
      const muted = await trigger.evaluate(node => getComputedStyle(node).color);
      await trigger.hover();
      await expect(page.getByRole("tooltip")).toHaveText(currentLabel(false, false));
      await trigger.click();
      await expect(popup).toBeVisible();
      await expect(trigger).not.toBeFocused();
      const pointerFocus = await trigger.evaluate(node => ({ visible: node.matches(":focus-visible"), shadow: getComputedStyle(node).boxShadow }));
      expect(pointerFocus).toEqual({ visible: false, shadow: "none" });
      await expect(popup.getByRole("radio", { name: basisLabels[0], exact: true })).toBeChecked();
      await expect(popup.getByRole("radio", { name: directionLabels[0], exact: true })).toBeChecked();
      const [anchor, floating] = await Promise.all([trigger.boundingBox(), popup.boundingBox()]);
      expect(floating!.y).toBeGreaterThanOrEqual(anchor!.y + anchor!.height);
      expect(floating!.x + floating!.width).toBeCloseTo(anchor!.x + anchor!.width, 0);
      await page.mouse.move(1000, 700);
      await page.screenshot({ path: testInfo.outputPath(`macro-sort-open-${locale}-${theme}.png`) });
      const frame = (await list.boundingBox())!;
      await page.screenshot({ path: testInfo.outputPath(`macro-sort-header-${locale}-${theme}.png`), clip: {
        x: frame.x, y: frame.y, width: frame.width, height: floating!.y + floating!.height - frame.y + 8,
      } });
      const seen: Partial<Record<Mode, Record<string, Summary>>> = {};

      async function expandGroups() {
        for (const heading of await list.locator('[data-slot=macro-group-heading][aria-expanded=false]').all()) await heading.click();
      }
      async function choose(label: string, mode: Mode) {
        const read = page.waitForResponse(response => {
          const url = new URL(response.url());
          return url.pathname === "/api/macro-tasks" && url.searchParams.get("sort") === mode;
        });
        await popup.getByRole("radio", { name: label, exact: true }).click();
        const response = await read;
        expect(response.ok()).toBe(true);
        const data = await response.json();
        expect(data.macroTasks.map((item: Summary) => item.macroTaskId)).toEqual(expected[mode]);
        seen[mode] = Object.fromEntries(data.macroTasks.map((item: Summary): [string, Summary] => [item.macroTaskId, item]));
        if (mode === "activity-desc") await expect(page).not.toHaveURL(/sort=/);
        else await expect(page).toHaveURL(new RegExp(`[?&]sort=${mode}$`));
        await expect(popup).toBeVisible();
        await expectShown(mode);
      }

      async function expectShown(mode: Mode) {
        const created = mode.startsWith("created");
        const label = basisLabels[created ? 1 : 0];
        const visible = expected[mode].filter(id => id === A || id === C);
        await expect.poll(() => times(page)).toEqual(visible.map(id => {
          const summary = seen[mode]![id]!;
          return [id, created ? summary.createdAt : summary.lastActivityAt, label];
        }));
      }

      await choose(basisLabels[1], "created-desc");
      await page.mouse.move(1000, 700);
      expect(await trigger.evaluate(node => getComputedStyle(node).color)).not.toBe(muted);
      await expect(trigger).toHaveAccessibleName(currentLabel(true, false));
      await choose(directionLabels[1], "created-asc");
      await expect(trigger.locator(".lucide-arrow-up-narrow-wide")).toHaveCount(1);
      await expect(trigger).toHaveAccessibleName(currentLabel(true, true));
      await page.keyboard.press("Escape");
      await expect(popup).toBeHidden();
      await expandGroups();
      // The other project and standalone row also show their creation stamps.
      const allTimes = await list.locator("[data-macro-task-id]").evaluateAll(nodes => nodes.map(node => {
        const time = node.querySelector("time")!;
        return [node.getAttribute("data-macro-task-id"), time.getAttribute("datetime"), time.querySelector(".sr-only")?.textContent];
      }));
      expect(allTimes).toHaveLength(4);
      for (const [id, time, label] of allTimes) {
        expect(time).toBe(seen["created-asc"]![id!]!.createdAt);
        expect(label).toBe(basisLabels[1]);
      }
      await collapse.click();
      await expect(list.locator('[data-slot=macro-group-heading][aria-expanded=true]')).toHaveCount(0);
      await collapse.click();
      await expect(list.locator('[data-slot=macro-group-heading][aria-expanded=true]')).toHaveCount(0);
      await page.reload();
      await trigger.click();
      await expect(popup.getByRole("radio", { name: basisLabels[1], exact: true })).toBeChecked();
      await expect(popup.getByRole("radio", { name: directionLabels[1], exact: true })).toBeChecked();
      await expect.poll(() => rows(page)).toEqual([C, A]);
      await expectShown("created-asc");
      await choose(basisLabels[0], "activity-asc");
      await choose(directionLabels[0], "activity-desc");
      await page.goBack();
      await expect(popup.getByRole("radio", { name: directionLabels[1], exact: true })).toBeChecked();
      await expect.poll(() => rows(page)).toEqual([C, A]);
      await expectShown("activity-asc");
      await page.goForward();
      await expect(popup.getByRole("radio", { name: directionLabels[0], exact: true })).toBeChecked();
      await expect.poll(() => rows(page)).toEqual([A, C]);
      await expectShown("activity-desc");
      await page.keyboard.press("Escape");
      // A pointer click leaves the icon button without a focus ring; Tab draws one.
      await collapse.click();
      expect(await collapse.evaluate(node => node.matches(":focus-visible"))).toBe(false);
      await page.keyboard.press("Shift+Tab");
      await expect(trigger).toBeFocused();
      await settle(page);
      const keyboardFocus = await trigger.evaluate(node => ({ visible: node.matches(":focus-visible"), shadow: getComputedStyle(node).boxShadow }));
      expect(keyboardFocus.visible).toBe(true);
      expect(keyboardFocus.shadow).not.toBe("none");
      expect(keyboardFocus.shadow).toContain("inset");
      await page.keyboard.press("Enter");
      await expect(popup).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(popup).toBeHidden();
      await trigger.click(); await trigger.click();
      await expect(popup).toBeHidden();
      await trigger.click(); await list.locator("[data-slot=macro-list-heading]").click();
      await expect(popup).toBeHidden();
    });
  });
}
