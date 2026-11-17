import { expect, type Locator, type Page } from "@playwright/test";
import { expectNoSidewaysScroll, open, settle, test } from "./preview";

/**
 * How the catalogue moves and answers the pointer: what opens and closes runs on
 * the shared Collapsible, a colour never jumps, hover never takes the look of a
 * selection away, a harness's head keeps its actions separate, and the controls band wraps
 * without leaving a button alone. Everything is read from computed style and from
 * boxes, never from waiting.
 */

const languages = [
  { locale: "zh-CN", settings: "设置", catalog: "模型", fold: /^(展开|收起) / },
  { locale: "en-US", settings: "settings", catalog: "Models", fold: /^(Fold|Unfold) / },
];
const CODEX = "codex / openai / gpt-5-codex";

const head = (page: Page, name: string) => page.locator(`[data-slot=harness-section][aria-label="${name}"] [data-slot=harness-header]`);

type Box = { x: number; y: number; width: number; height: number };
async function box(target: Locator): Promise<Box> {
  const found = await target.boundingBox();
  expect(found, "the element is drawn").not.toBeNull();
  return found!;
}

async function labelToUnderline(target: Locator, textSelector?: string): Promise<number> {
  return target.evaluate((node, selector) => {
    const text = selector ? node.querySelector(selector) : node.firstChild;
    const range = document.createRange();
    range.selectNodeContents(text!);
    const words = range.getBoundingClientRect();
    const link = node.getBoundingClientRect();
    const after = getComputedStyle(node, "::after");
    const underlineTop = link.bottom - parseFloat(after.bottom) - parseFloat(after.height);
    // A text Range measures the font's ink box. Include its half-leading to
    // measure the line box requested by the layout contract.
    const lineHeight = parseFloat(getComputedStyle(text instanceof Element ? text : node).lineHeight);
    const lineBottom = (words.top + words.bottom + lineHeight) / 2;
    return underlineTop - lineBottom;
  }, textSelector);
}

/** Seconds in a computed `transition-duration` or `animation-duration`, one per listed property. */
const seconds = (value: string) => value.split(",").map(part => parseFloat(part));
async function transition(target: Locator) {
  return target.evaluate(node => {
    const style = getComputedStyle(node);
    return { property: style.transitionProperty, duration: style.transitionDuration };
  });
}
/** A colour change takes 100 to 150ms. */
async function expectColourTransition(target: Locator, label: string) {
  const { property, duration } = await transition(target);
  expect(property, `${label} has a transition`).not.toBe("none");
  for (const value of seconds(duration)) {
    expect(value, `${label}: ${duration}`).toBeGreaterThanOrEqual(0.1);
    expect(value, `${label}: ${duration}`).toBeLessThanOrEqual(0.15);
  }
}

for (const { locale, fold } of languages) {
  test.describe(`the catalogue's gestures in ${locale} at 1280px`, () => {
    test.use({ locale, viewport: { width: 1280, height: 900 } });

    test("covers the whole head with one trigger beneath the arrow and provider retries", async ({ page }) => {
      await open(page, "#buddies/models");
      for (const section of await page.locator("[data-slot=harness-section]").all()) {
        const name = (await section.getAttribute("aria-label"))!;
        const header = head(page, name);
        await expect(header.getByRole("button")).toHaveCount(name === "DSH" ? 2 : 1);
        const trigger = header.getByRole("button", { name: new RegExp(`${fold.source}${name}$`) });
        const [triggerBox, headerBox] = await Promise.all([box(trigger), box(header)]);
        expect(triggerBox, `${name}: the trigger covers the entire header`).toEqual(headerBox);
        const shape = await box(header.locator("[data-slot=disclosure]"));
        expect(Math.min(shape.width, shape.height), `${name}: the chevron is drawn large enough to see`).toBeGreaterThanOrEqual(14);
        expect(await trigger.locator("button").count()).toBe(0);
        expect(await header.locator("button button").count()).toBe(0);
        if (name === "DSH") {
          const provider = header.locator("[data-slot=header-provider]").filter({ hasText: "deepseek-official" });
          await expect(provider.locator("[data-slot=cooling-status]")).toHaveCount(1);
          await expect(provider.getByRole("button", { name: locale === "zh-CN" ? "开放重试" : "Open retry" })).toHaveCount(1);
        }
        // No second arrow at the right end: the head has one chevron.
        await expect(header.locator("[data-slot=disclosure]")).toHaveCount(1);
      }
    });

    test("folds from the name, provider and whitespace, and from Enter and Space", async ({ page }) => {
      await open(page, "#buddies/models");
      const header = head(page, "Codex");
      const trigger = header.getByRole("button", { name: new RegExp(`${fold.source}Codex$`) });
      const models = page.locator("[data-slot=harness-section][aria-label=Codex] [data-slot=model-row]");
      const details = page.locator("[data-slot=harness-details]:visible");
      await expect(trigger).toHaveAttribute("aria-expanded", "true");
      await expect(details).toHaveCount(0);
      await expect(models.first()).toBeVisible();
      const mark = header.locator("[data-slot=disclosure]");
      await expect(mark).toHaveAttribute("data-state", "open");
      await expect(mark).toHaveCSS("rotate", "90deg");
      // Hit the visible words by their boxes: pointer-events lets the trigger beneath receive the click.
      for (const words of [header.getByText("Codex", { exact: true }), header.locator("[data-slot=header-provider] > span").first()]) {
        const target = await box(words);
        await page.mouse.click(target.x + target.width / 2, target.y + target.height / 2);
        await expect(trigger).toHaveAttribute("aria-expanded", "false");
        await expect(mark).toHaveAttribute("data-state", "closed");
        await expect(mark).toHaveCSS("rotate", "none");
        await expect(models).toHaveCount(0);
        await expect(details).toHaveCount(0);
        const reopening = await box(words);
        await page.mouse.click(reopening.x + reopening.width / 2, reopening.y + reopening.height / 2);
        await expect(trigger).toHaveAttribute("aria-expanded", "true");
        await expect(mark).toHaveCSS("rotate", "90deg");
      }
      // The bottom-left padding has no text or retry control over it.
      const bounds = await box(header);
      await page.mouse.click(bounds.x + 1, bounds.y + bounds.height - 1);
      await expect(trigger).toHaveAttribute("aria-expanded", "false");
      await trigger.focus();
      await page.keyboard.press("Enter");
      await expect(trigger).toHaveAttribute("aria-expanded", "true");
      await page.keyboard.press("Space");
      await expect(trigger).toHaveAttribute("aria-expanded", "false");
      await expect(details).toHaveCount(0);
      await page.locator('[data-slot="buddy-header"] a[href="#buddies/harness"]').click();
      const cards = page.locator("[data-slot=harness-card]");
      expect(await cards.count()).toBeGreaterThan(0);
      for (const card of await cards.all()) {
        await expect(card).toBeVisible();
        const groups = card.locator("[data-slot=harness-details] > [role=group]");
        await expect(groups).toHaveCount(2);
        const [left, right] = [await box(groups.nth(0)), await box(groups.nth(1))];
        expect(Math.abs(left.y - right.y)).toBeLessThanOrEqual(1);
        expect(right.x).toBeGreaterThanOrEqual(left.x + left.width);
      }
      await expectNoSidewaysScroll(page);
    });

    test("moves every opening and closing on the Collapsible for 200ms on the height Radix measures", async ({ page }) => {
      await open(page, "#buddies/models");
      const section = page.locator("[data-slot=harness-section][aria-label=Codex]");
      const trigger = head(page, "Codex").getByRole("button", { name: new RegExp(`${fold.source}Codex$`) });
      const id = (await trigger.getAttribute("aria-controls"))!;
      // One step in the page: click, let one frame pass for React to commit, then read what is moving
      // while it still is: the animation is the Collapsible's, 200ms long, on the height Radix measured.
      const motion = (node: Element, contentId: string) => {
        (node as HTMLElement).click();
        return new Promise(resolve => requestAnimationFrame(() => {
          const target = document.getElementById(contentId)!;
          const style = getComputedStyle(target);
          resolve({
            state: target.getAttribute("data-state"), name: style.animationName, duration: style.animationDuration,
            height: style.getPropertyValue("--radix-collapsible-content-height"),
            running: target.getAnimations().map(animation => (animation as CSSAnimation).animationName),
          });
        }));
      };
      const closing = await trigger.evaluate(motion, id) as { state: string; name: string; duration: string; height: string; running: string[] };
      expect(closing).toMatchObject({ state: "closed", name: "disclosure-up", duration: "0.2s", running: ["disclosure-up"] });
      expect(parseFloat(closing.height)).toBeGreaterThan(0);
      await expect(section.locator("[data-slot=model-row]")).toHaveCount(0);
      const opened = await trigger.evaluate(motion, id) as { state: string; name: string; duration: string; running: string[] };
      expect(opened).toMatchObject({ state: "open", name: "disclosure-down", duration: "0.2s", running: ["disclosure-down"] });
      await expect(page.locator("[data-slot=harness-details]:visible")).toHaveCount(0);
      await page.getByRole("button", { name: CODEX, exact: true }).click();
      const drawer = page.getByRole("region", { name: CODEX, exact: true });
      await expect(drawer).toBeVisible();
      const drawerStyle = await drawer.locator("xpath=ancestor::*[@data-slot='collapsible-content'][1]").evaluate(node => {
        const style = getComputedStyle(node);
        return { duration: style.animationDuration, state: node.getAttribute("data-state") };
      });
      expect(drawerStyle).toEqual({ duration: "0.2s", state: "open" });
      // The chevron turns with it, on a transform transition of the same length.
      const turn = await head(page, "Codex").locator("[data-slot=disclosure]").evaluate(node => {
        const style = getComputedStyle(node);
        return { property: style.transitionProperty, duration: style.transitionDuration };
      });
      expect(turn.property).toContain("transform");
      expect(seconds(turn.duration)[0]).toBe(0.2);
      const model = page.getByRole("button", { name: CODEX, exact: true });
      const chevron = model.locator("[data-slot=model-chevron]");
      await settle(page);
      await expect(chevron).toHaveCSS("rotate", "180deg");
      await model.click();
      await settle(page);
      await expect(chevron).toHaveCSS("rotate", "none");
      await expect(page.locator("[data-slot=harness-details]:visible")).toHaveCount(0);
    });

    test("keeps the service drawer scoped to service and buddy settings with usable controls", async ({ page }) => {
      await open(page, "#buddies/models");
      await page.getByRole("button", { name: CODEX, exact: true }).click();
      const drawer = page.getByRole("region", { name: CODEX, exact: true });
      await expect(drawer).toBeVisible();
      // The sections above are still opening: measure when everything has stopped moving.
      await settle(page);
      await expect(drawer.locator("[data-slot=model-profile], [data-slot=kind-evaluation], [data-slot=model-correspondence]")).toHaveCount(0);
      // Controls are 32px, the small ones 28px; nothing a pointer must hit is smaller. A checkbox is a mark inside its label.
      const buttons = page.getByRole("main").locator("button:not([role=checkbox]), [role=switch], [role=radio], input:not([type=checkbox]), select");
      for (const control of await buttons.all()) {
        if (!(await control.isVisible())) continue;
        const target = await box(control);
        expect(target.height, (await control.getAttribute("aria-label")) ?? (await control.textContent()) ?? "control").toBeGreaterThanOrEqual(28);
      }
    });

    test("changes every colour on a transition of 100 to 150ms", async ({ page }) => {
      await open(page, "#buddies/models");
      await expectColourTransition(page.locator("[data-slot=model-row] > button").first(), "a model row's button");
      await expectColourTransition(page.locator("[data-slot=fold-handle]").first(), "a harness header");
      await expectColourTransition(page.locator("[data-slot=catalogue-actions] > button").first(), "a toolbar button");
      await expectColourTransition(page.locator("[data-slot=checkbox]").first(), "a filter");
      await expectColourTransition(page.getByRole("main").getByRole("navigation").getByRole("link").first(), "a section link");
      await expectColourTransition(page.getByRole("banner").getByRole("navigation").getByRole("link").first(), "a top bar link");
      await page.getByRole("button", { name: CODEX, exact: true }).click();
      const drawer = page.getByRole("region", { name: CODEX, exact: true });
      await expectColourTransition(drawer.locator("[data-slot=buddy-choice]").first(), "a buddy choice");
      await expectColourTransition(drawer.getByRole("switch").first(), "a switch");
      await expectColourTransition(drawer.locator("[data-slot=segmented-item]").first(), "a segmented choice");
      // The selection moves between buddy rows on that transition.
      // Cells are taken by place: a locator by `aria-pressed` would follow the state it changes.
      const cells = drawer.locator("[data-slot=buddy-stop]");
      const pressed = await cells.evaluateAll(nodes => nodes.map(node => node.getAttribute("aria-pressed") === "true"));
      const target = pressed.findIndex(value => !value);
      expect(target, "a cell that is not selected").toBeGreaterThanOrEqual(0);
      await cells.nth(target).click();
      await expect(cells.nth(target)).toHaveAttribute("aria-pressed", "true");
      await expect(drawer.locator("[data-slot=buddy-stop][aria-pressed=true]")).toHaveCount(1);
    });
  });

  test.describe(`the catalogue with less motion in ${locale} at 1280px`, () => {
    test.use({ locale, viewport: { width: 1280, height: 900 }, reducedMotion: "reduce" });

    test("makes no transition and opens and closes at once", async ({ page }) => {
      await open(page, "#buddies/models");
      for (const [label, target] of [
        ["a model row's button", page.locator("[data-slot=model-row] > button").first()],
        ["a harness header", page.locator("[data-slot=fold-handle]").first()],
        ["a toolbar button", page.locator("[data-slot=catalogue-actions] > button").first()],
        ["a section link", page.getByRole("main").getByRole("navigation").getByRole("link").first()],
        ["a chevron's shape", page.locator("[data-slot=disclosure]").first()],
      ] as const) expect((await transition(target)).property, `${label} makes no transition`).toBe("none");
      const trigger = head(page, "Codex").getByRole("button", { name: new RegExp(`${fold.source}Codex$`) });
      const id = (await trigger.getAttribute("aria-controls"))!;
      const closing = await trigger.evaluate((node, contentId) => {
        (node as HTMLElement).click();
        // One frame for React to commit; Radix keeps the (hidden) element but not what is in it.
        return new Promise<{ animations: number; hidden: boolean; display: string; children: number }>(resolve => requestAnimationFrame(() => {
          const target = document.getElementById(contentId)!;
          resolve({ animations: target.getAnimations().length, hidden: target.hasAttribute("hidden"), display: getComputedStyle(target).display, children: target.children.length });
        }));
      }, id);
      // Not even a frame of closing: nothing runs, and the content is already gone.
      expect(closing).toEqual({ animations: 0, hidden: true, display: "none", children: 0 });
      await expect(page.locator("[data-slot=harness-details]:visible")).toHaveCount(0);
    });
  });

  for (const theme of ["light", "dark"] as const) {
    test.describe(`what is open stays open under the pointer in ${locale} in ${theme}`, () => {
      test.use({ locale, viewport: { width: 1280, height: 900 } });
      test.beforeEach(async ({ page }) => {
        await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      });

      test("gives the entire header model-row hover and keyboard focus", async ({ page }) => {
        await page.emulateMedia({ reducedMotion: "reduce" });
        await open(page, "#buddies/models");
        const header = head(page, "Codex");
        const trigger = header.locator("[data-slot=fold-handle]");
        const model = page.getByRole("button", { name: CODEX, exact: true });
        const resting = await trigger.evaluate(node => getComputedStyle(node).backgroundColor);
        await trigger.hover({ position: { x: 1, y: 1 } });
        await expect(trigger).toHaveCSS("cursor", "pointer");
        const hovered = await trigger.evaluate(node => getComputedStyle(node).backgroundColor);
        expect(hovered).not.toBe(resting);
        await model.hover();
        await expect(model).toHaveCSS("background-color", hovered);
        await trigger.click({ position: { x: 1, y: 1 } });
        await expect(trigger).toHaveAttribute("aria-expanded", "false");
        const closedHover = await trigger.evaluate(node => getComputedStyle(node).backgroundColor);
        expect(closedHover).not.toBe(resting);
        await trigger.press("Tab");
        await page.keyboard.press("Shift+Tab");
        await expect(trigger).toBeFocused();
        expect(await trigger.evaluate(node => node.matches(":focus-visible"))).toBe(true);
        expect(await trigger.evaluate(node => getComputedStyle(node).boxShadow)).not.toBe("none");
      });

      test("keeps an open model row and selected buddy visible under the pointer", async ({ page }) => {
        await open(page, "#buddies/models");
        await page.getByRole("button", { name: CODEX, exact: true }).click();
        const row = page.locator("[data-slot=model-row]").filter({ has: page.getByRole("button", { name: CODEX, exact: true }) });
        const button = row.getByRole("button");
        const other = page.locator("[data-slot=model-row]").filter({ hasNot: page.getByRole("button", { name: CODEX, exact: true }) }).first();
        const paint = (target: Locator) => target.evaluate(node => {
          const style = getComputedStyle(node);
          return { background: style.backgroundColor, border: style.borderLeftColor, shadow: style.boxShadow, opacity: style.opacity };
        });
        // Read once every transition has ended: two reads in one frame always agree, so equal reads prove nothing.
        const settled = async (target: Locator) => {
          await settle(page);
          return paint(target);
        };
        await page.mouse.move(0, 0);
        const rowAt = await settled(row), buttonAt = await settled(button);
        const otherButton = other.getByRole("button");
        const otherAt = await settled(otherButton);
        await button.hover();
        const rowHover = await settled(row), buttonHover = await settled(button);
        // The row button carries selection; its wrapper keeps the common row divider.
        expect(rowHover.background).toBe(rowAt.background);
        expect(rowHover.border).toBe(rowAt.border);
        expect(buttonAt.background).not.toBe("rgba(0, 0, 0, 0)");
        await expect(button).toHaveAttribute("aria-expanded", "true");
        expect(buttonHover.background).toBe(buttonAt.background);
        expect(buttonHover.background).not.toBe("rgba(0, 0, 0, 0)");
        // And it is not what a row that is not open becomes under the pointer.
        await otherButton.hover();
        const otherHover = await settled(otherButton);
        expect(otherHover.background).not.toBe(otherAt.background);
        expect(otherHover.background).not.toBe(buttonHover.background);
        expect((await settled(other)).border).toBe(rowAt.border);
        expect((await settled(button)).background).toBe(buttonAt.background);
        // The selected row keeps its full background under the pointer.
        const drawer = page.getByRole("region", { name: CODEX, exact: true });
        const selected = drawer.locator("[data-slot=buddy-stop][aria-pressed=true]");
        const unselected = drawer.locator("[data-slot=buddy-stop][aria-pressed=false]").first();
        await page.mouse.move(0, 0);
        const selectedChoice = selected.locator("xpath=ancestor::*[@data-slot='buddy-choice'][1]");
        const unselectedChoice = unselected.locator("xpath=ancestor::*[@data-slot='buddy-choice'][1]");
        const selectedAt = await settled(selectedChoice), unselectedAt = await settled(unselectedChoice);
        await expect(selectedChoice).toHaveAttribute("data-selected", "true");
        expect(selectedAt.background).not.toBe("rgba(0, 0, 0, 0)");
        await selected.hover();
        const selectedHover = await settled(selectedChoice);
        expect(selectedHover.background).toBe(selectedAt.background);
        await unselected.hover();
        expect((await settled(unselectedChoice)).background).not.toBe(unselectedAt.background);
        expect((await settled(selectedChoice)).background).toBe(selectedAt.background);
        await expect(drawer.locator("[data-slot=buddy-pointer]")).toHaveCount(0);
      });
    });
  }
}

for (const { locale, catalog } of languages) for (const width of [700, 800, 900, 1000]) {
  test.describe(`the controls band in ${locale} at ${width}px`, () => {
    test.use({ locale, viewport: { width, height: 900 } });

    test("keeps model discovery with the filters when controls wrap", async ({ page }) => {
      await open(page, "#buddies/models");
      const toolbar = page.locator("[data-slot=catalogue-toolbar]");
      const controls = toolbar.locator("[data-slot=catalogue-controls]");
      const items = [
        { name: "search", target: controls.getByRole("searchbox"), button: false },
        ...(await controls.locator("[data-slot=catalogue-filters] label").all()).map((target, index) => ({ name: `filter ${index}`, target, button: false })),
        ...(await controls.locator("[data-slot=catalogue-actions] > button").all()).map((target, index) => ({ name: `button ${index}`, target, button: true })),
      ];
      const placed = await Promise.all(items.map(async item => ({ ...item, at: await box(item.target) })));
      // Items on one row share their top, within the rounding of the layout.
      const rows: typeof placed[] = [];
      for (const item of [...placed].sort((a, b) => a.at.y - b.at.y || a.at.x - b.at.x)) {
        const row = rows.find(candidate => Math.abs(candidate[0].at.y + candidate[0].at.height / 2 - (item.at.y + item.at.height / 2)) < 12);
        if (row) row.push(item); else rows.push([item]);
      }
      for (const row of rows) expect(row.length === 1 && row[0].button, `${row[0].name} is alone on its row`).toBe(false);
      const buttons = placed.filter(item => item.button);
      expect(buttons).toHaveLength(1);
      await expect(buttons[0].target).toHaveAccessibleName(locale === "zh-CN" ? "发现模型" : "Discover models");
      for (const item of placed) {
        expect(item.at.x, item.name).toBeGreaterThanOrEqual(0);
        expect(item.at.x + item.at.width, item.name).toBeLessThanOrEqual(width + 0.5);
      }
      await expect(page.locator("[data-slot=buddy-header]").getByRole("link").nth(1)).toContainText(catalog);
      await expectNoSidewaysScroll(page);
    });

    test("keeps the current section's name with its underline, wherever the band wraps", async ({ page }) => {
      await open(page, "#buddies/models");
      const current = page.locator("[data-slot=buddy-header] a[aria-current=page]");
      await expect(current).toHaveAttribute("aria-current", "page");
      const geometry = await current.evaluate(node => {
        const after = getComputedStyle(node, "::after");
        const range = document.createRange();
        range.selectNode(node.firstChild!);
        const text = range.getBoundingClientRect();
        const link = node.getBoundingClientRect();
        const header = node.closest('[data-slot="buddy-header"]')!;
        const band = header.getBoundingClientRect();
        const border = parseFloat(getComputedStyle(header).borderBottomWidth);
        return { textMiddle: (text.top + text.bottom) / 2, rowMiddle: (band.top + band.bottom - border) / 2,
          underlineBottom: link.bottom - parseFloat(after.bottom), dividerBottom: band.bottom,
          underlineHeight: parseFloat(after.height) };
      });
      expect(Math.abs(geometry.textMiddle - geometry.rowMiddle), "the name is vertically centered in its row").toBeLessThanOrEqual(1);
      expect(Math.abs(geometry.underlineBottom - geometry.dividerBottom), "the underline rests on the row's bottom divider").toBeLessThanOrEqual(0.5);
      expect(geometry.underlineHeight).toBe(2);
    });
  });
}

const underlinedViews = [
  { name: "top navigation", hash: "#tasks", selector: 'header nav a[aria-current="page"]', height: "header" },
  { name: "task filters", hash: "#tasks", selector: '[data-slot="macro-filter-tabs"] [data-slot="tabs-trigger"][data-state="active"]', textSelector: '[data-slot="macro-filter-name"]' },
  { name: "Harness section", hash: "#buddies/harness", selector: '[data-slot="buddy-header"] a[aria-current="page"]', height: '[data-slot="buddy-header"]' },
  { name: "Buddy sections", hash: "#buddies/models", selector: '[data-slot="buddy-header"] a[aria-current="page"]', height: '[data-slot="buddy-header"]' },
  { name: "evaluation detail", hash: "#buddies/evaluation/codex/00000000-0000-4000-8000-000000000003/high", selector: '[data-slot="evaluation-detail"] nav a[aria-current="page"]' },
];
for (const { locale } of languages) for (const theme of ["dark", "light"] as const) for (const width of [1440, 390]) {
  test.describe(`underlined navigation at ${width}px in ${locale} ${theme}`, () => {
    test.use({ locale, viewport: { width, height: 900 }, reducedMotion: "reduce" });
    for (const view of underlinedViews) test(`keeps ${view.name} labels five to seven pixels above their underline`, async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, view.hash);
      const label = page.locator(view.selector);
      await expect(label).toBeVisible();
      if (view.height) expect((await box(page.locator(view.height))).height).toBe(36);
      const gap = await labelToUnderline(label, view.textSelector);
      expect(gap).toBeGreaterThanOrEqual(5);
      expect(gap).toBeLessThanOrEqual(7);
      await expectNoSidewaysScroll(page);
    });
  });
}

/** What a reader of English meets on the Buddy page and the bar above it: no Chinese, in text or in any attribute that is read. */
test.describe("the Buddy page in English", () => {
  test.use({ locale: "en-US", viewport: { width: 1280, height: 900 } });

  async function chinese(page: Page): Promise<string[]> {
    return page.evaluate(() => {
      const cjk = /[　-〿一-鿿＀-￯]/;
      const found: string[] = [];
      // Domain names and task titles are the user's content, in any language.
      const inDomain = (node: Node | null) => node instanceof Element && node.closest('[data-slot=domain-row], a[href^="#tasks/"]') !== null;
      for (const element of document.body.querySelectorAll("*")) {
        if (inDomain(element)) continue;
        for (const name of ["aria-label", "aria-description", "placeholder", "title", "alt"]) {
          const value = element.getAttribute(name);
          if (value && cjk.test(value)) found.push(`<${element.tagName.toLowerCase()} ${name}> ${value}`);
        }
      }
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (!inDomain(node.parentElement) && cjk.test(node.textContent ?? "")) found.push(`text: ${node.textContent}`);
      }
      return found;
    });
  }

  test("says nothing in Chinese in the catalogue, its sections, drawers, dialogs and hints, nor in the bar", async ({ page }) => {
    await open(page, "#buddies/models");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    expect(await chinese(page)).toEqual([]);
    const folded = page.locator("[data-slot=fold-handle][aria-expanded=false]");
    while (await folded.count() > 0) await folded.first().click();
    expect(await chinese(page)).toEqual([]);
    for (const row of await page.locator("[data-slot=model-row] > button").all()) {
      const name = (await row.getAttribute("aria-label"))!;
      await row.click();
      // The drawer of this row only: the one that closed before it is still leaving.
      const cells = page.getByRole("region", { name, exact: true }).locator("[data-slot=buddy-stop]");
      await expect(cells.first()).toBeVisible();
      for (let index = 0, count = await cells.count(); index < count; index++) await cells.nth(index).click();
      await settle(page);
      expect(await chinese(page), name).toEqual([]);
    }
    await page.locator('[data-slot="buddy-header"]').getByRole("link", { name: "Harnesses", exact: true }).click();
    await expect(page.locator("[data-slot=harness-card]").first()).toBeVisible();
    expect(await chinese(page)).toEqual([]);
    for (const name of ["Set path"]) {
      await page.getByRole("button", { name, exact: true }).first().click();
      await expect(page.getByRole("dialog")).toBeVisible();
      expect(await chinese(page), name).toEqual([]);
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog")).toHaveCount(0);
    }
  });

  test("says nothing in Chinese in routing and the dialog that adds a Router", async ({ page }) => {
    await open(page, "#buddies/router/routing");
    const roles = page.getByRole("region", { name: "Router", exact: true });
    await expect(roles).toBeVisible();
    expect(await chinese(page)).toEqual([]);
    await roles.getByRole("button", { name: "Add", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    expect(await chinese(page)).toEqual([]);
  });
});
