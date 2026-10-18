import { expect } from "@playwright/test";
import { TOOLBAR_ITEMS, contrastOf, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, settle, test } from "./preview";

const FLASH = "dsh / deepseek-official / deepseek-flash";
const CODEX = "codex / openai / gpt-5-codex";

const languages = [
  { locale: "zh-CN", catalog: "目录", unsaved: "未保存的修改", settings: "设置", account: "账户", check: "检测", checked: "已检测 Codex：已找到", cooling: "冷却中", clear: "解除冷却", unavailable: /^显示不可用/ },
  { locale: "en-US", catalog: "Catalog", unsaved: "Unsaved changes", settings: "settings", account: "account", check: "Check", checked: "Checked Codex: Found", cooling: "Cooling down", clear: "Clear cooldown", unavailable: /^Show unavailable/ },
];

for (const { locale, catalog, unsaved, settings, account, check, checked, cooling, clear, unavailable } of languages)
  for (const width of [1280, 390]) for (const theme of ["light", "dark"] as const) {
    test.describe(`the catalogue in ${locale} at ${width}px in ${theme}`, () => {
      // The contrast is measured at rest: under reduced motion the page makes no transitions.
      test.use({ locale, viewport: { width, height: 900 }, reducedMotion: "reduce" });
      test.beforeEach(async ({ page }) => {
        await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      });

      test("keeps harness summaries on one line and model rows inside the page", async ({ page }) => {
        await open(page, "#buddies/catalog");
        const directory = page.getByRole("region", { name: catalog, exact: true });
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        await expect(page.locator("[data-slot=model-drawer]")).toHaveCount(0);
        await expectWholeAndApart(page.locator("[data-slot=harness-section]").first().locator(".."), page.locator("[data-slot=harness-section]"));
        const toolbar = page.locator("[data-slot=catalogue-toolbar]");
        await expectWholeAndApart(directory, toolbar.locator(TOOLBAR_ITEMS));
        const search = toolbar.getByRole("searchbox");
        expect(await search.evaluate(node => getComputedStyle(node).fontSize)).toBe("14px");
        const navBox = (await toolbar.getByRole("navigation").boundingBox())!;
        const searchBox = (await search.boundingBox())!;
        const countBox = (await toolbar.getByRole("status").boundingBox())!;
        if (width === 390) {
          expect(countBox.y).toBeGreaterThanOrEqual(navBox.y);
          expect(countBox.y).toBeLessThan(navBox.y + navBox.height);
          expect(searchBox.y).toBeGreaterThanOrEqual(navBox.y + navBox.height);
          expect(searchBox.width).toBe(width - 24);
          const filtersBox = (await toolbar.locator("[data-slot=catalogue-filters]").boundingBox())!;
          expect(filtersBox.y).toBeGreaterThanOrEqual(searchBox.y + searchBox.height);
          for (const button of await toolbar.locator("[data-slot=catalogue-actions] > button").all()) {
            const box = (await button.boundingBox())!;
            expect(box.y).toBeGreaterThanOrEqual(searchBox.y + searchBox.height);
            expect(box.height).toBe(28);
          }
        } else expect(searchBox.x).toBeGreaterThan(navBox.x + navBox.width + 24);
        await expect(toolbar.getByRole("link").first()).toHaveAccessibleName(catalog);
        await expect(page.locator("[data-slot=unsaved-mark]")).toHaveCount(0);
        const metered = page.getByRole("button", { name: `DSH ${account}`, exact: true });
        await expect(metered.locator("[data-slot=cooling-status]")).toContainText(cooling);
        await expect(metered).not.toContainText("%");
        const logo = page.locator("[data-slot=harness-section][aria-label=ZCode] [data-slot=harness-logo]");
        const mask = await logo.evaluate(node => {
          const style = getComputedStyle(node);
          return { image: style.maskImage, color: style.color, fill: style.backgroundColor };
        });
        expect(mask.image).not.toBe("none");
        expect(mask.image).toContain("url(");
        expect(mask.fill).toBe(mask.color);
        expect(await contrastOf(logo.locator(".."))).toBeGreaterThanOrEqual(4.5);
        for (const row of await page.locator("[data-slot=model-row]").all()) {
          await expectWholeAndApart(row, row.locator(":scope > button"));
          await expectTextUncut(row.locator(":scope > button"));
        }
        for (const summary of await page.locator("[data-slot=harness-section]").getByRole("button", { name: new RegExp(` ${account}$`) }).all()) {
          await expectTextUncut(summary);
          // The row remains the button, and the cooldown summary belongs to it.
          await expect(summary.locator("[data-slot=cooling-status]")).toHaveCount(1);
          expect(await contrastOf(summary)).toBeGreaterThanOrEqual(4.5);
        }
        const zcode = page.getByRole("button", { name: `ZCode ${account}`, exact: true });
        await expect(zcode.locator("[data-slot=cooling-status]")).toBeEmpty();
        await expectTextUncut(zcode);
        // The available sections precede the recorded login failure.
        await expect(page.locator("[data-slot=harness-section]").last()).toHaveAttribute("aria-label", "Claude Code");
        const unavailableSection = page.getByRole("region", { name: "Claude Code", exact: true });
        await expect(unavailableSection.getByRole("button", { name: /^(展开|Expand|Unfold) Claude Code$/ })).toHaveAttribute("aria-expanded", "false");
        await expect(unavailableSection.locator("[data-slot=model-row]")).toHaveCount(0);
        await expect(unavailableSection).toContainText(locale === "zh-CN" ? "未登录" : "Not logged in");
        await page.getByRole("checkbox", { name: unavailable }).click();
        const offline = page.locator("[data-slot=harness-section]").last();
        await offline.getByRole("button", { name: /^(展开|Expand|Unfold) Claude Code$/ }).click();
        for (const row of await offline.locator("[data-slot=model-row]").all()) {
          await expectTextUncut(row.getByRole("button"));
          for (const word of await row.locator("span").filter({ hasText: /\S/ }).all()) {
            if (!(await word.locator("*").count())) expect(await contrastOf(word)).toBeGreaterThanOrEqual(4.5);
          }
        }
        await expectNoSidewaysScroll(page);
      });

      test("shows one cooling provider and a quiet non-cooling provider in the account details", async ({ page }, testInfo) => {
        await open(page, "#buddies/catalog");
        await page.getByRole("button", { name: `DSH ${account}`, exact: true }).click();
        await page.getByRole("button", { name: `Codex ${account}`, exact: true }).click();
        const dsh = page.getByRole("group", { name: `DSH ${settings}`, exact: true });
        const codex = page.getByRole("group", { name: `Codex ${settings}`, exact: true });
        await expect(dsh.locator("[data-slot=cooling-details]")).toContainText("deepseek-official");
        await expect(dsh.locator("[data-slot=cooling-details]")).not.toContainText("deepseek-partner");
        await expect(dsh.getByRole("button", { name: clear })).toHaveCount(1);
        await expect(codex.locator("[data-slot=cooling-details]")).toBeEmpty();
        await expect(codex.getByRole("button", { name: clear })).toHaveCount(0);
        await expectNoSidewaysScroll(page);
        await page.screenshot({ path: testInfo.outputPath(`catalogue-cooling-and-clear-${locale}-${width}-${theme}.png`), fullPage: true });
        await dsh.screenshot({ path: testInfo.outputPath(`buddy-cooling-${locale}-${width}-${theme}.png`) });
        await codex.screenshot({ path: testInfo.outputPath(`buddy-clear-${locale}-${width}-${theme}.png`) });
      });

      test("aligns the effort strip and selected panel, stacking the same drawer on narrow screens", async ({ page }) => {
        await open(page, "#buddies/catalog");
        await page.getByRole("button", { name: CODEX, exact: true }).click();
        const drawer = page.getByRole("region", { name: CODEX, exact: true });
        const strip = drawer.getByRole("group", { name: /^(推理强度|Reasoning effort)$/ });
        const panel = drawer.locator("[data-slot=buddy-panel]");
        await expect(drawer.getByRole("radiogroup", { name: /^(默认偏好|Default preference)$/ })).toHaveCount(0);
        await expect(panel.getByRole("radiogroup", { name: /的偏好$|^Preference of / })).toHaveCount(1);
        await expect(panel.getByRole("radio", { name: locale === "zh-CN" ? "不设" : "None" })).toBeVisible();
        await expectWholeAndApart(drawer, drawer.locator(":scope > div > div"));
        await expectWholeAndApart(strip, strip.getByRole("button"));
        await expectWholeAndApart(panel, panel.locator("input, [role=radiogroup], [role=switch]"));
        await expectTextUncut(drawer.locator("button, [role=radio]"));
        for (const heading of await drawer.locator("h3").all())
          expect(await heading.evaluate(node => getComputedStyle(node).fontSize)).toBe("12px");
        for (const input of await drawer.locator("input").all())
          expect(await input.evaluate(node => getComputedStyle(node).fontSize)).toBe("14px");
        const [stripBox, panelBox] = [await strip.boundingBox(), await panel.boundingBox()];
        expect(Math.round(stripBox!.x)).toBe(Math.round(panelBox!.x));
        expect(Math.round(stripBox!.width)).toBe(Math.round(panelBox!.width));
        if (width === 390) {
          expect(await strip.evaluate(node => getComputedStyle(node).gridTemplateColumns.split(" ").length)).toBe(3);
          const [model, buddy] = await drawer.locator(":scope > div > div").all();
          const [modelBox, buddyBox] = [await model.boundingBox(), await buddy.boundingBox()];
          expect(modelBox!.y + modelBox!.height).toBeLessThanOrEqual(buddyBox!.y);
        }
        const enabled = panel.getByRole("switch");
        const wasEnabled = await enabled.getAttribute("aria-checked");
        await strip.getByRole("button", { name: "medium", exact: true }).click();
        await expect(panel.getByRole("switch")).toHaveAttribute("aria-checked", "false");
        await strip.getByRole("button", { name: "high", exact: true }).click();
        await expect(panel.getByRole("switch")).toHaveAttribute("aria-checked", wasEnabled!);
        const selected = strip.getByRole("button", { pressed: true });
        const point = await selected.locator("..").locator("[data-slot=buddy-pointer]").evaluate(node => {
          const shape = getComputedStyle(node);
          const style = getComputedStyle(node.previousElementSibling!);
          return { display: shape.display, border: shape.borderRightColor, color: style.color };
        });
        if (width === 390) expect(point.display).toBe("none");
        else {
          expect(point.display).not.toBe("none");
          expect(point.border).toBe(point.color);
        }
        // A hint-wrapped Radix control renders its choice from aria-checked.
        const fill = (checked: boolean) => drawer.getByRole("radio", { checked }).first().evaluate(node => getComputedStyle(node).backgroundColor);
        expect(await fill(true)).not.toBe(await fill(false));
        for (const text of await drawer.locator("label, h3, [role=radio], [data-slot=buddy-stop]").all())
          expect(await contrastOf(text), await text.innerText()).toBeGreaterThanOrEqual(4.5);
        await expectNoSidewaysScroll(page);
      });

      test("shows unsaved marks on a setting, an effort and the closed row, and keeps the save bar at the bottom", async ({ page }) => {
        await open(page, "#buddies/catalog");
        await page.getByRole("button", { name: FLASH, exact: true }).click();
        const drawer = page.getByRole("region", { name: FLASH, exact: true });
        await drawer.getByRole("switch").click();
        const bar = page.getByRole("region", { name: unsaved });
        const box = await bar.boundingBox();
        expect(box!.x).toBe(0);
        expect(box!.width).toBe(width);
        expect(box!.y + box!.height).toBe(page.viewportSize()!.height);
        await expectWholeAndApart(bar, bar.locator(":scope > *"));
        expect(await contrastOf(bar.getByRole("status"))).toBeGreaterThanOrEqual(4.5);
        await expect(drawer.locator("[data-slot=buddy-stop]").getByRole("img")).toHaveCount(1);
        await expect(drawer.locator("label [role=img]")).toHaveCount(0);
        await page.getByRole("button", { name: FLASH, exact: true }).click();
        await expect(page.locator("[data-slot=model-drawer]")).toHaveCount(0);
        await expect(page.getByRole("button", { name: FLASH, exact: true }).getByRole("img")).toHaveCount(1);
        await expectNoSidewaysScroll(page);
      });

      test("expands accounts independently and keeps checks and hints inside the window", async ({ page }) => {
        // The hint opens with its motion, as a user without the setting sees it.
        await page.emulateMedia({ reducedMotion: "no-preference" });
        await open(page, "#buddies/catalog");
        await page.getByRole("button", { name: `Codex ${account}`, exact: true }).click();
        await page.getByRole("button", { name: `DSH ${account}`, exact: true }).click();
        const codex = page.getByRole("group", { name: `Codex ${settings}`, exact: true });
        await expect(page.getByRole("group", { name: `DSH ${settings}`, exact: true })).toBeVisible();
        await expectWholeAndApart(codex, codex.getByRole("group"));
        for (const heading of await codex.locator("h3").all())
          expect(await heading.evaluate(node => getComputedStyle(node).fontSize)).toBe("12px");
        await codex.getByRole("button", { name: check, exact: true }).click();
        await expect(page.locator("[data-slot=harness-feedback]")).toHaveText(checked);
        await expectWholeAndApart(page.getByRole("main"), page.locator("[data-slot=harness-feedback]"));
        // Account guidance may be absent; the recorded review word always has a hint.
        const review = codex.locator("[aria-description]").filter({ hasText: /^(可用|Eligible)$/ }).first();
        await expect(review).toHaveAttribute("aria-description", /本地资格检查不调用模型|The local eligibility check calls no model/);
        // Scrolling closes hints: bring the word into view and let scrolling settle before keyboard focus.
        await review.scrollIntoViewIfNeeded();
        await settle(page);
        await codex.getByRole("button", { name: check, exact: true }).focus();
        await settle(page);
        await page.keyboard.press("Shift+Tab");
        await expect(review).toBeFocused();
        const hint = page.locator("[data-slot=tooltip-content][data-state$=open]");
        await expect(hint).toBeVisible();
        expect((await hint.boundingBox())!.width).toBeLessThanOrEqual(320);
        await expectWholeAndApart(page.locator("body"), hint);
        await expectNoSidewaysScroll(page);
      });
    });
  }
