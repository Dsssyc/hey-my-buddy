import { expect } from "@playwright/test";
import { expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, settle, test } from "./preview";
import { settingsFixture } from "./settings-fixtures";

const words = {
  "zh-CN": { sections: ["主题", "语言", "提醒", "访问", "版本", "存储"], theme: "主题", dark: "深色", language: "语言", english: "English", input: "静默提醒阈值（分钟）", description: "一次执行这么久没有任何进展，会标为可能卡住。", check: "检查占用", reclaim: "回收…", details: "详细信息", current: "撤销当前浏览器", cancel: "取消" },
  "en-US": { sections: ["Theme", "Language", "Reminders", "Access", "Version", "Storage"], theme: "Theme", dark: "Dark", language: "Language", english: "English", input: "Silence reminder threshold (minutes)", description: "An execution without progress for this long is marked as possibly stuck.", check: "Check usage", reclaim: "Reclaim…", details: "Details", current: "Revoke current browser", cancel: "Cancel" },
};
for (const locale of ["zh-CN", "en-US"] as const) for (const width of [1280, 800, 390]) for (const theme of ["light", "dark"] as const) {
  test.describe(`settings ${locale} ${width}px ${theme}`, () => {
    test.use({ locale, viewport: { width, height: 800 }, colorScheme: theme });
    test("keeps sections, controls, disclosures and confirmation whole", async ({ page }) => {
      const w = words[locale];
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await settingsFixture(page);
      await open(page, "#settings");
      const settings = page.locator("[data-slot=settings-page]");
      await expect(settings.getByRole("heading", { level: 2 })).toHaveText(w.sections);
      await expect(settings.getByRole("spinbutton", { name: w.input })).toHaveValue("15");
      await expect(settings.locator("p").filter({ hasText: w.description })).toBeVisible();
      const language = settings.getByRole("radiogroup", { name: w.language, exact: true });
      await expect(language.getByRole("radio", { name: "中文", exact: true })).toBeVisible();
      await expect(language.getByRole("radio", { name: "English", exact: true })).toBeVisible();
      await expectTextUncut(settings.locator("h2, button:not([role=switch]), label"));
      for (const group of await settings.getByRole("radiogroup").all()) await expectWholeAndApart(group, group.getByRole("radio"));
      await settings.getByRole("button", { name: w.check, exact: true }).click();
      await expect(settings.getByRole("table").last()).toBeVisible();
      expect(await settings.getByRole("table").last().getByRole("row").count()).toBeGreaterThan(3);
      const table = settings.getByRole("table").last();
      await expectTextUncut(table.locator("th, td, button"));
      const byteLines = await table.locator("td:nth-child(3) [tabindex], td:nth-child(4) [tabindex]").evaluateAll(nodes => nodes.map(node => {
        const range = document.createRange(); range.selectNodeContents(node); return range.getClientRects().length;
      }));
      expect(byteLines.every(lines => lines === 1)).toBe(true);

      const headers = await table.locator("thead th").evaluateAll(nodes => nodes.map(node => {
        const range = document.createRange(); range.selectNodeContents(node);
        const box = range.getBoundingClientRect(); return { left: box.left, right: box.right };
      }));
      for (let index = 1; index < headers.length; index++) expect(headers[index].left - headers[index - 1].right).toBeGreaterThanOrEqual(8);

      await settings.getByRole("button", { name: w.details, exact: true }).click();
      await settle(page);
      await expect(settings.getByRole("button", { name: w.details, exact: true })).toHaveAttribute("aria-expanded", "true");
      await settings.getByRole("button", { name: w.reclaim, exact: true }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog.getByRole("button", { name: w.cancel, exact: true })).toBeFocused();
      await expectWholeAndApart(page.locator("body"), dialog);
      await expectTextUncut(dialog.locator("h2, p, button"));
      await dialog.getByRole("button", { name: w.cancel, exact: true }).click();
      await expect(settings.getByRole("button", { name: w.reclaim, exact: true })).toBeFocused();
      await settings.getByRole("button", { name: w.current, exact: true }).click();
      await expect(page.getByRole("dialog").getByRole("button", { name: w.cancel, exact: true })).toBeFocused();
      await expectWholeAndApart(page.locator("body"), page.getByRole("dialog"));
      await page.keyboard.press("Escape");
      await expectNoSidewaysScroll(page);
      if (locale === "en-US") expect((await settings.innerText()).replace("中文", "")).not.toMatch(/[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/);
      const controls = await settings.locator("button[data-slot=button], [role=radiogroup], input").evaluateAll(nodes => nodes.map(node => Math.round(node.getBoundingClientRect().height)));
      expect(controls.every(height => height === 28 || height === 32), JSON.stringify(controls)).toBe(true);
      expect(await settings.locator("[title], [disabled]").count()).toBe(0);
    });
  });
}

test.describe("settings transitions and persistence", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1280, height: 800 } });
  test("changes every visible label immediately, remembers choices and supports keyboard theme selection", async ({ page }) => {
    await settingsFixture(page);
    await open(page, "#settings");
    await page.getByRole("radiogroup", { name: "语言" }).getByRole("radio", { name: "English", exact: true }).click();
    await expect(page.getByRole("navigation").getByRole("link").last()).toHaveText("Settings");
    const theme = page.getByRole("radiogroup", { name: "Theme", exact: true });
    await theme.getByRole("radio", { name: "Light", exact: true }).focus();
    await page.keyboard.press("ArrowRight", { delay: 50 });
    await expect(theme.getByRole("radio", { name: "Dark", exact: true })).toHaveAttribute("aria-checked", "true");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.reload();
    await expect(page.getByRole("radiogroup", { name: "Language" }).getByRole("radio", { name: "English", exact: true })).toHaveAttribute("aria-checked", "true");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  });
  test("keeps a tall list of browsers scrollable and every browser row inside the page", async ({ page }) => {
    await page.route("**/api/console", async route => {
      const response = await route.fetch();
      const data = await response.json();
      data.consoleAccess = { requireLogin: true, revision: 2, sessions: Array.from({ length: 16 }, (_, index) => ({ id: index.toString(16).padStart(24, "0"), current: index === 0, lastSeen: 1_792_080_000 })) };
      await route.fulfill({ response, json: data });
    });
    await open(page, "#settings");
    const scroll = page.locator("[data-slot=settings-page]").locator("xpath=ancestor::*[@data-slot='scroll-area-viewport']");
    await page.getByRole("region", { name: "存储", exact: true }).scrollIntoViewIfNeeded();
    expect(await scroll.evaluate(node => node.scrollHeight > node.clientHeight && node.scrollTop > 0)).toBe(true);
    await expectTextUncut(page.locator("[data-slot=settings-page] td, [data-slot=settings-page] th"));
    await expectNoSidewaysScroll(page);
  });
});

for (const reduce of [false, true]) test.describe(`settings motion ${reduce ? "reduced" : "normal"}`, () => {
  test.use({ locale: "zh-CN", viewport: { width: 1280, height: 800 }, reducedMotion: reduce ? "reduce" : "no-preference" });
  test("animates both disclosure directions and selection, respecting the system preference", async ({ page }) => {
    await settingsFixture(page);
    await open(page, "#settings");
    const version = page.getByRole("region", { name: "版本", exact: true });
    const trigger = version.getByRole("button", { name: "详细信息", exact: true });
    for (const state of ["open", "closed"]) {
      await trigger.click();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
      if (reduce) {
        expect(await version.locator("[data-slot=collapsible-content]").evaluateAll(nodes => nodes.flatMap(node => node.getAnimations()))).toEqual([]);
      } else {
        const midpoint = await version.locator("[data-slot=collapsible-content]").evaluate(node => {
          const animation = node.getAnimations()[0];
          const duration = Number(animation.effect!.getTiming().duration);
          animation.pause(); animation.currentTime = duration / 2;
          const style = getComputedStyle(node);
          const measured = { duration, state: node.getAttribute("data-state"), height: node.getBoundingClientRect().height, full: Number.parseFloat(style.getPropertyValue("--radix-collapsible-content-height")), opacity: Number(style.opacity) };
          animation.finish();
          return measured;
        });
        expect(midpoint.duration).toBe(200);
        expect(midpoint.state).toBe(state);
        expect(midpoint.height).toBeGreaterThan(0);
        expect(midpoint.height).toBeLessThan(midpoint.full);
        expect(midpoint.opacity).toBeGreaterThan(0);
        expect(midpoint.opacity).toBeLessThan(1);
      }
      await settle(page);
    }
    const dark = page.getByRole("radiogroup", { name: "主题", exact: true }).getByRole("radio", { name: "深色", exact: true });
    if (reduce) expect(await dark.evaluate(node => getComputedStyle(node).transitionProperty)).toBe("none");
    else expect(await dark.evaluate(node => getComputedStyle(node).transitionDuration)).toBe("0.15s");
    await dark.click();
    await expect(dark).toHaveAttribute("aria-checked", "true");
  });
});

test.describe("settings browser authorization commands", () => {
  test.use({ locale: "zh-CN" });
  test("revokes the selected browser and changes access through the current server fields", async ({ page }) => {
    const commands = await settingsFixture(page);
    await open(page, "#settings");
    await page.getByRole("button", { name: "撤销浏览器 bbbbbbbb", exact: true }).click();
    await expect(page.getByRole("button", { name: "撤销浏览器 bbbbbbbb", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "撤销当前浏览器", exact: true })).toBeVisible();
    expect(commands).toContainEqual({ operation: "console_session_revoke", params: { sessionId: "b".repeat(24) } });
    const toggle = page.getByRole("switch", { name: "只允许已授权的浏览器" });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(commands).toContainEqual({ operation: "console_access_set", params: { requireLogin: false } });
    await expect(page.getByRole("list", { name: "已授权的浏览器" })).toHaveCount(0);
  });
});
