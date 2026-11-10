import { expect } from "@playwright/test";
import { TOOLBAR_ITEMS, contrastOf, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, settle, test } from "./preview";

const FLASH = "dsh / deepseek-official / deepseek-flash";
const CODEX = "codex / openai / gpt-5-codex";

const languages = [
  { locale: "zh-CN", catalog: "目录", unsaved: "未保存的修改", settings: "设置", check: "检测", checked: "已检测 Codex：已找到", cooling: "冷却中", clear: "开放重试", unavailable: /^显示不可用/ },
  { locale: "en-US", catalog: "Catalog", unsaved: "Unsaved changes", settings: "settings", check: "Check", checked: "Checked Codex: Found", cooling: "Cooling down", clear: "Open retry", unavailable: /^Show unavailable/ },
];

for (const { locale, catalog, unsaved, settings, check, checked, cooling, clear, unavailable } of languages)
  for (const width of [1440]) for (const theme of ["light", "dark"] as const) {
    test.describe(`the catalogue in ${locale} at ${width}px in ${theme}`, () => {
      // The contrast is measured at rest: under reduced motion the page makes no transitions.
      test.use({ locale, viewport: { width, height: 900 }, reducedMotion: "reduce" });
      test.beforeEach(async ({ page }) => {
        await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      });

      test("uses the whole model row as its disclosure handle", async ({ page }) => {
        await open(page, "#buddies/catalog");
        const row = page.getByRole("button", { name: CODEX, exact: true });
        const resting = await row.evaluate(node => getComputedStyle(node).backgroundColor);
        await expect(row).toHaveAttribute("aria-expanded", "false");
        await expect(row.locator("[data-slot=disclosure]")).toHaveCount(0);
        await expect(page.locator("[data-slot=harness-header] [data-slot=disclosure]").first()).toBeVisible();
        await row.hover();
        await expect(row).toHaveCSS("cursor", "pointer");
        expect(await row.evaluate(node => getComputedStyle(node).backgroundColor)).not.toBe(resting);
        await row.focus();
        await page.keyboard.press("Shift+Tab");
        await page.keyboard.press("Tab");
        await expect(row).toBeFocused();
        expect(await row.evaluate(node => getComputedStyle(node).boxShadow)).not.toBe("none");
        await row.press("Enter");
        await page.mouse.move(0, 0);
        await expect(row).toHaveAttribute("aria-expanded", "true");
        const contentId = await row.getAttribute("aria-controls");
        expect(contentId).toBeTruthy();
        await expect(page.locator(`[id="${contentId}"]`)).toContainText("gpt-5-codex");
        const selected = await row.evaluate(node => {
          const reference = document.createElement("div");
          reference.style.backgroundColor = "var(--past-wash)";
          document.body.append(reference);
          const color = getComputedStyle(reference).backgroundColor;
          reference.remove();
          return { row: getComputedStyle(node).backgroundColor, color };
        });
        expect(selected.row).toBe(selected.color);
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
        const header = page.locator("[data-slot=buddy-header]");
        await expect(header.getByRole("link").first()).toHaveAccessibleName(catalog);
        await expect(toolbar.getByRole("status")).toHaveCount(0);
        await expect(page.locator("[data-slot=unsaved-mark]")).toHaveCount(0);
        const metered = page.locator("[data-slot=harness-section][aria-label=DSH] [data-slot=harness-header]");
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
        for (const summary of await page.locator("[data-slot=harness-header]").all()) {
          await expectTextUncut(summary);
          expect(await contrastOf(summary)).toBeGreaterThanOrEqual(4.5);
        }
        const zcode = page.locator("[data-slot=harness-section][aria-label=ZCode] [data-slot=harness-header]");
        await expect(zcode.locator("[data-slot=cooling-status]")).toHaveCount(0);
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

      test("keeps ordinary model rows compact and cooling inline", async ({ page }, testInfo) => {
        await open(page, "#buddies/catalog");
        const cooled = page.getByRole("button", { name: FLASH, exact: true }).locator("..");
        const ordinary = page.getByRole("button", { name: "dsh / deepseek-partner / deepseek-flash", exact: true }).locator("..");
        await expect(cooled.locator("[data-slot=cooling-status]")).toHaveCount(0);
        await expect(cooled.locator("[data-slot=model-provider]")).toHaveText("deepseek-official");
        await expect(ordinary.locator("[data-slot=model-provider]")).toHaveText("deepseek-partner");
        const [coolingBox, ordinaryBox] = [(await cooled.boundingBox())!, (await ordinary.boundingBox())!];
        expect(ordinaryBox.height).toBe(41);
        expect(coolingBox.height).toBe(ordinaryBox.height);
        await expectNoSidewaysScroll(page);
        await testInfo.attach("model-row-heights", { body: JSON.stringify({ locale, width, theme,
          withoutCooling: ordinaryBox.height, withCooling: coolingBox.height }), contentType: "application/json" });
        await page.getByRole("region", { name: "DSH", exact: true }).screenshot({
          path: testInfo.outputPath(`buddy-inline-${locale}-${width}-${theme}.png`) });
      });

      test("shows cooling retry in the head and two sidebar detail blocks", async ({ page }, testInfo) => {
        await open(page, "#buddies/catalog");
        const dsh = page.getByRole("group", { name: `DSH ${settings}`, exact: true });
        const codex = page.getByRole("group", { name: `Codex ${settings}`, exact: true });
        await expect(dsh.locator(":scope > [role=group]")).toHaveCount(2);
        await expect(codex.locator(":scope > [role=group]")).toHaveCount(2);
        const dshHeader = page.locator("[data-slot=harness-section][aria-label=DSH] [data-slot=harness-header]");
        await expect(dshHeader).toContainText("deepseek-official");
        await expect(dshHeader.getByRole("button", { name: clear })).toHaveCount(1);
        await expect(page.locator("[data-slot=harness-section][aria-label=Codex] [data-slot=harness-header]").getByRole("button", { name: clear })).toHaveCount(0);
        await expectNoSidewaysScroll(page);
        await page.screenshot({ path: testInfo.outputPath(`catalogue-cooling-and-clear-${locale}-${width}-${theme}.png`), fullPage: true });
        await dsh.screenshot({ path: testInfo.outputPath(`buddy-cooling-${locale}-${width}-${theme}.png`) });
        await codex.screenshot({ path: testInfo.outputPath(`buddy-clear-${locale}-${width}-${theme}.png`) });
      });

      test("places model service above a buddy list and selected panel", async ({ page }) => {
        await open(page, "#buddies/catalog");
        await page.getByRole("button", { name: CODEX, exact: true }).click();
        const drawer = page.getByRole("region", { name: CODEX, exact: true });
        const service = drawer.locator("[data-slot=model-service]");
        const columns = drawer.locator("[data-slot=buddy-columns]");
        const list = columns.locator("[data-slot=buddy-list]");
        const panel = drawer.locator("[data-slot=buddy-panel]");
        await expect(service.locator("[data-slot=model-service-line]")).toHaveCount(1);
        await expect(list.locator("[data-slot=buddy-choice]")).toHaveCount(await list.locator("[data-slot=buddy-stop]").count());
        const choices = list.locator("[data-slot=buddy-choice]");
        await expect(choices.first().getByRole("switch")).toHaveCount(1);
        await expect(drawer.getByRole("radiogroup", { name: /^(默认偏好|Default preference)$/ })).toHaveCount(0);
        await expect(panel.getByRole("radiogroup", { name: /的偏好$|^Preference of / })).toHaveCount(1);
        await expect(panel.getByRole("radio", { name: locale === "zh-CN" ? "不设" : "None" })).toBeVisible();
        await expectWholeAndApart(drawer, service);
        await expectWholeAndApart(columns, list);
        await expectWholeAndApart(columns, panel);
        await expect(panel.getByRole("switch")).toHaveCount(0);
        await expect(panel.locator("[data-slot=buddy-infrastructure-failures]")).toHaveCount(0);
        await expectTextUncut(drawer.locator("button, [role=radio]"));
        for (const heading of await drawer.locator("h3").all())
          expect(await heading.evaluate(node => getComputedStyle(node).fontSize)).toBe("12px");
        for (const input of await drawer.locator("input").all())
          expect(await input.evaluate(node => getComputedStyle(node).fontSize)).toBe("14px");
        const [serviceBox, listBox, panelBox] = [await service.boundingBox(), await list.boundingBox(), await panel.boundingBox()];
        expect(serviceBox!.y + serviceBox!.height).toBeLessThanOrEqual(listBox!.y);
        expect(listBox!.x + listBox!.width).toBeLessThanOrEqual(panelBox!.x + 1);
        const selected = choices.locator("[data-slot=buddy-stop][aria-pressed=true]");
        await expect(selected).toHaveCount(1);
        await choices.locator("[data-slot=buddy-stop][aria-pressed=false]").first().click();
        await expect(list.locator("[data-slot=buddy-choice][data-selected=true]")).toHaveCount(1);
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
        await drawer.locator("[data-slot=buddy-choice] [role=switch]").first().click();
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

      test("keeps sidebar checks and hints inside the window", async ({ page }) => {
        // The hint opens with its motion, as a user without the setting sees it.
        await page.emulateMedia({ reducedMotion: "no-preference" });
        await open(page, "#buddies/catalog");
        const codex = page.getByRole("group", { name: `Codex ${settings}`, exact: true });
        await expect(page.getByRole("group", { name: `DSH ${settings}`, exact: true })).toBeVisible();
        await expectWholeAndApart(codex, codex.locator(":scope > [role=group]"));
        for (const heading of await codex.locator("h4").all())
          expect(await heading.evaluate(node => getComputedStyle(node).fontSize)).toBe("12px");
        await codex.getByRole("button", { name: check, exact: true }).click();
        await expect(page.locator("[data-slot=harness-feedback]")).toHaveText(checked);
        await expectWholeAndApart(page.getByRole("main"), page.locator("[data-slot=harness-feedback]"));
        // Account guidance may be absent; the recorded review word always has a hint.
        const review = codex.locator("[aria-description]").filter({ hasText: /^(强制限制|Enforced)$/ }).first();
        await expect(review).toHaveAttribute("aria-description", /按 harness 的只读档位声明|Based on the harness/);
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
