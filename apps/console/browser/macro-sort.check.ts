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

for (const [locale, sortLabel, collapseLabel, collapseListLabel, basisLabels, directionLabels] of [
  ["zh-CN", "排序", "全部折叠", "收起宏任务列表", ["最近更新", "创建时间"], ["降序", "升序"]],
  ["en-US", "Sort", "Collapse all", "Collapse macro task list", ["Last updated", "Created"], ["Descending", "Ascending"]],
] as const) for (const theme of ["light", "dark"] as const) {
  test.describe(`macro sort ${locale} ${theme}`, () => {
    test.use({ locale, viewport: { width: 1280, height: 800 }, reducedMotion: "reduce" });

    test("keeps the four toolbar buttons alike and changes each sort choice on its own", async ({ page }, testInfo) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, "#tasks");
      const toolbar = page.locator("[data-slot=macro-list-toolbar] > div").last();
      const basis = page.locator("[data-slot=macro-sort-basis]");
      const direction = page.locator("[data-slot=macro-sort-direction]");
      const popup = page.locator("[data-slot=macro-sort-popover]");
      const collapse = page.getByRole("button", { name: collapseLabel, exact: true });
      const fold = page.getByRole("button", { name: collapseListLabel, exact: true });
      const list = page.locator("[data-slot=macro-list]");
      const basisName = (created: boolean) => `${sortLabel}${locale === "zh-CN" ? "：" : ": "}${basisLabels[created ? 1 : 0]}`;
      const buttons = toolbar.locator("button");

      // The four actions sit in one right-hand row, in order and with one shape.
      await expect(buttons).toHaveCount(4);
      await expect(fold).toBeVisible();
      expect(await buttons.evaluateAll(nodes => nodes.map(node =>
        [node.getAttribute("aria-label"), node.getAttribute("data-variant"), node.getAttribute("data-size")])))
        .toEqual([
          [basisName(false), "ghost", "icon-sm"], [directionLabels[0], "ghost", "icon-sm"],
          [collapseLabel, "ghost", "icon-sm"], [collapseListLabel, "ghost", "icon-sm"],
        ]);
      const boxes = await buttons.evaluateAll(nodes => nodes.map(node => {
        const { x, y, width, height } = node.getBoundingClientRect();
        return { x, y, width, height };
      }));
      for (const box of boxes) {
        expect(box.width).toBeCloseTo(28, 0);
        expect(box.height).toBeCloseTo(28, 0);
      }
      for (let index = 1; index < boxes.length; index++) {
        expect(boxes[index].y).toBeCloseTo(boxes[0].y, 0);
        expect(boxes[index].x - (boxes[index - 1].x + boxes[index - 1].width)).toBeCloseTo(4, 0);
      }

      await expect(basis).toHaveAccessibleName(basisName(false));
      await expect(direction).toHaveAccessibleName(directionLabels[0]);
      await expect(basis).not.toHaveAttribute("data-emphasized", "true");
      await expect(direction).not.toHaveAttribute("data-emphasized", "true");
      await expect(basis.locator(".lucide-history")).toHaveCount(1);
      await expect(direction.locator(".lucide-arrow-down-wide-narrow")).toHaveCount(1);
      const muted = await basis.evaluate(node => getComputedStyle(node).color);
      const frame = (await list.boundingBox())!;
      await page.screenshot({ path: testInfo.outputPath(`macro-sort-toolbar-${locale}-${theme}.png`), clip: {
        x: frame.x, y: frame.y, width: frame.width, height: 180,
      } });

      await basis.hover();
      await expect(page.getByRole("tooltip")).toHaveText(basisName(false));
      await page.mouse.move(1000, 700);
      await direction.hover();
      await expect(page.getByRole("tooltip")).toHaveText(directionLabels[0]);
      await page.mouse.move(1000, 700);
      await expect(page.getByRole("tooltip")).toBeHidden();

      await basis.click();
      await expect(popup).toBeVisible();
      await expect(popup.getByRole("radio")).toHaveCount(2);
      await expect(popup.getByRole("radio")).toHaveText([...basisLabels]);
      await expect(popup.locator("[data-slot=segmented]")).toHaveCount(0);
      await expect(popup.locator("[role=separator]")).toHaveCount(0);
      await expect(popup.getByRole("radio", { name: basisLabels[0], exact: true })).toBeChecked();
      await expect(basis).not.toBeFocused();
      const pointerFocus = await basis.evaluate(node => ({ visible: node.matches(":focus-visible"), shadow: getComputedStyle(node).boxShadow }));
      expect(pointerFocus).toEqual({ visible: false, shadow: "none" });
      const [anchor, floating] = await Promise.all([basis.boundingBox(), popup.boundingBox()]);
      expect(floating!.y).toBeGreaterThanOrEqual(anchor!.y + anchor!.height);
      expect(floating!.x + floating!.width).toBeCloseTo(anchor!.x + anchor!.width, 0);
      await page.mouse.move(1000, 700);
      await page.screenshot({ path: testInfo.outputPath(`macro-sort-popover-${locale}-${theme}.png`), clip: {
        x: frame.x, y: frame.y, width: frame.width, height: floating!.y + floating!.height - frame.y + 8,
      } });

      const seen: Partial<Record<Mode, Record<string, Summary>>> = {};

      async function expandGroups() {
        for (const heading of await list.locator('[data-slot=macro-group-heading][aria-expanded=false]').all()) await heading.click();
      }
      async function record(mode: Mode, action: () => Promise<void>) {
        const read = page.waitForResponse(response => {
          const url = new URL(response.url());
          return url.pathname === "/api/macro-tasks" && url.searchParams.get("sort") === mode;
        });
        await action();
        const response = await read;
        expect(response.ok()).toBe(true);
        const data = await response.json();
        expect(data.macroTasks.map((item: Summary) => item.macroTaskId)).toEqual(expected[mode]);
        seen[mode] = Object.fromEntries(data.macroTasks.map((item: Summary): [string, Summary] => [item.macroTaskId, item]));
        if (mode === "activity-desc") await expect(page).not.toHaveURL(/sort=/);
        else await expect(page).toHaveURL(new RegExp(`[?&]sort=${mode}$`));
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

      // Choosing a basis leaves the direction alone and keeps the menu open.
      await record("created-desc", () => popup.getByRole("radio", { name: basisLabels[1], exact: true }).click());
      await expect(popup).toBeVisible();
      await expect(basis).toHaveAccessibleName(basisName(true));
      await expect(basis).toHaveAttribute("data-emphasized", "true");
      await expect(direction).not.toHaveAttribute("data-emphasized", "true");
      await expect(basis.locator(".lucide-calendar-plus")).toHaveCount(1);
      expect(await basis.evaluate(node => getComputedStyle(node).color)).not.toBe(muted);
      await page.keyboard.press("Escape");
      await expect(popup).toBeHidden();

      // The direction button toggles on a click and opens nothing.
      await record("created-asc", () => direction.click());
      await expect(popup).toBeHidden();
      await expect(direction).toHaveAccessibleName(directionLabels[1]);
      await expect(direction).toHaveAttribute("data-emphasized", "true");
      await expect(direction.locator(".lucide-arrow-up-narrow-wide")).toHaveCount(1);
      await expect(basis).toHaveAttribute("data-emphasized", "true");
      await record("created-desc", () => direction.click());
      await expect(direction).not.toHaveAttribute("data-emphasized", "true");
      await expect(basis).toHaveAttribute("data-emphasized", "true");

      // Returning the basis to its default clears only that button's emphasis.
      await basis.click();
      await record("activity-desc", () => popup.getByRole("radio", { name: basisLabels[0], exact: true }).click());
      await expect(popup).toBeVisible();
      await expect(basis).not.toHaveAttribute("data-emphasized", "true");
      await expect(direction).not.toHaveAttribute("data-emphasized", "true");
      await page.keyboard.press("Escape");
      await expect(popup).toBeHidden();

      // An ascending direction is the only emphasized button from here.
      await record("activity-asc", () => direction.click());
      await expect(direction).toHaveAttribute("data-emphasized", "true");
      await expect(basis).not.toHaveAttribute("data-emphasized", "true");
      await collapse.click();
      await expect(list.locator('[data-slot=macro-group-heading][aria-expanded=true]')).toHaveCount(0);

      // The URL resumes the same row order after a reload and through history.
      await page.reload();
      await expect(basis).toHaveAccessibleName(basisName(false));
      await expect(direction).toHaveAccessibleName(directionLabels[1]);
      await expect(basis).not.toHaveAttribute("data-emphasized", "true");
      await expect(direction).toHaveAttribute("data-emphasized", "true");
      await expandGroups();
      await expect.poll(() => rows(page)).toEqual([C, A]);
      await expectShown("activity-asc");
      await page.goBack();
      await expect.poll(() => rows(page)).toEqual([A, C]);
      await expect(direction).toHaveAccessibleName(directionLabels[0]);
      await expectShown("activity-desc");
      await page.goForward();
      await expect.poll(() => rows(page)).toEqual([C, A]);
      await expect(direction).toHaveAccessibleName(directionLabels[1]);
      await expectShown("activity-asc");

      // A pointer click leaves the icon button without a focus ring; Tab draws one.
      await collapse.click();
      expect(await collapse.evaluate(node => node.matches(":focus-visible"))).toBe(false);
      await page.keyboard.press("Shift+Tab");
      await expect(direction).toBeFocused();
      await page.keyboard.press("Shift+Tab");
      await expect(basis).toBeFocused();
      await settle(page);
      const keyboardFocus = await basis.evaluate(node => ({ visible: node.matches(":focus-visible"), shadow: getComputedStyle(node).boxShadow }));
      expect(keyboardFocus.visible).toBe(true);
      expect(keyboardFocus.shadow).not.toBe("none");
      expect(keyboardFocus.shadow).toContain("inset");
      await page.keyboard.press("Enter");
      await expect(popup).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(popup).toBeHidden();
      await basis.click(); await basis.click();
      await expect(popup).toBeHidden();
      await basis.click(); await list.locator("[data-slot=macro-list-heading]").click();
      await expect(popup).toBeHidden();
    });
  });
}
