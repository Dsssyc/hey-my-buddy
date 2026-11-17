import { expect, type Locator } from "@playwright/test";
import type { Snapshot } from "../src/types";
import { expectNoSidewaysScroll, expectTextUncut, open, test } from "./preview";

/** Measure rendered label lines and the shared grid, including the help button. */
async function settingsGeometry(settings: Locator) {
  return settings.locator(':scope > [data-slot="router-field"]').evaluateAll(nodes => nodes.map(node => {
    const label = document.getElementById(node.getAttribute("aria-labelledby")!)!;
    const range = document.createRange();
    range.selectNodeContents(label);
    return { x: node.getBoundingClientRect().x, y: node.getBoundingClientRect().y,
      lines: range.getClientRects().length, labelWidth: node.firstElementChild!.getBoundingClientRect().width,
      controlLeft: node.lastElementChild!.getBoundingClientRect().left };
  }));
}

for (const theme of ["dark", "light"] as const) for (const locale of ["zh-CN", "en-US"] as const) {
  for (const width of [1280, 1440]) test.describe(`Buddy followups ${theme} ${locale} ${width}px`, () => {
    test.use({ locale, viewport: { width, height: 1000 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme));

    test("keeps all three Router title lines 16px above their first card and fits shared setting labels", async ({ page }, testInfo) => {
      const measurements = [];
      await open(page, "#buddies/router/routing");
      for (const work of ["routing", "maintenance", "profiles"]) {
        await page.locator(`[data-slot="router-works"] a[href="#buddies/router/${work}"]`).click();
        const title = page.locator('[data-slot="router-works"] [aria-current="page"]');
        const firstCard = page.locator('[data-slot="router-detail"] [data-slot="router-card"]').first();
        await expect(firstCard).toBeVisible();
        const titleBox = (await title.boundingBox())!;
        const cardBox = (await firstCard.boundingBox())!;
        const gap = cardBox.y - titleBox.y - titleBox.height;
        expect(Math.abs(gap - 16), `${work}: title line to first card`).toBeLessThanOrEqual(1);
        const settings = page.locator('[data-slot="router-detail"] .router-settings');
        const fields = await settingsGeometry(settings);
        expect(fields.length).toBeGreaterThan(0);
        for (const field of fields) expect(field.lines, `${work}: field label is one line`).toBe(1);
        for (const field of fields) for (const other of fields.filter(other => Math.abs(other.x - field.x) <= 1)) {
          expect(Math.abs(field.controlLeft - other.controlLeft)).toBeLessThanOrEqual(1);
          expect(Math.abs(field.labelWidth - other.labelWidth)).toBeLessThanOrEqual(1);
        }
        if (work === "routing") {
          expect(fields).toHaveLength(4);
          expect(fields[0].y).toBeCloseTo(fields[1].y, 0);
          expect(fields[0].x).toBeCloseTo(fields[2].x, 0);
          expect(fields[1].x).toBeCloseTo(fields[3].x, 0);
          expect(fields[1].x).toBeGreaterThan(fields[0].x);
        }
        measurements.push({ work, gap, fields });
        await expectNoSidewaysScroll(page);
      }
      await testInfo.attach("router-layout", { body: JSON.stringify(measurements), contentType: "application/json" });
    });
  });

  test.describe(`Buddy followups catalog ${theme} ${locale}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme));

    test("draws proportional occupancy meters only for reported models and starts location directly below the title", async ({ page }) => {
      const reported = [
        { harness: "codex", provider: "openai", model: "gpt-5-codex", active: 1, limit: 4 },
        { harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", active: 0, limit: 2 },
        { harness: "dsh", provider: "deepseek-partner", model: "deepseek-flash", active: 3, limit: 2 },
      ];
      await page.route("**/api/console", async route => {
        const response = await route.fetch();
        const snapshot: Snapshot = await response.json();
        snapshot.modelServiceConcurrency = reported;
        await route.fulfill({ response, json: snapshot });
      });
      await open(page, "#buddies/models");
      for (const header of await page.locator('[data-slot="harness-header"]').all()) {
        if (await header.getByRole("button").first().getAttribute("aria-expanded") === "true")
          await expect(header).not.toContainText(/并发|Concurrency/);
      }
      // The same content-sized column must fit both the desktop and narrow layout.
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 1000 });
        for (const entry of reported) {
          const row = page.getByRole("button", { name: `${entry.harness} / ${entry.provider} / ${entry.model}`, exact: true });
          const label = locale === "zh-CN" ? `占用 ${entry.active} / ${entry.limit}` : `In use ${entry.active} / ${entry.limit}`;
          const occupancy = row.getByLabel(label, { exact: true });
          await expect(occupancy).toHaveText(`${entry.active} / ${entry.limit}`);
          const meter = occupancy.locator('[data-slot="model-occupancy-meter"]');
          const geometry = await meter.evaluate(node => {
            const box = node.getBoundingClientRect(), fill = node.firstElementChild!;
            return { width: box.width, height: box.height, filled: fill.getBoundingClientRect().width,
              background: getComputedStyle(node).backgroundColor, fill: getComputedStyle(fill).backgroundColor };
          });
          expect(geometry.width).toBe(48);
          expect(geometry.height).toBe(6);
          expect(geometry.filled).toBeCloseTo(48 * Math.min(1, entry.active / entry.limit), 1);
          await expect(meter).toHaveCSS("background-color", await meter.evaluate(node => {
            const sample = document.createElement("span"); sample.style.color = "var(--line)";
            node.append(sample); const color = getComputedStyle(sample).color; sample.remove(); return color;
          }));
          expect(geometry.background).not.toBe(geometry.fill);
          await expect(meter.locator(":scope > span")).toHaveCSS("background-color", await occupancy.evaluate(node => getComputedStyle(node).color));
          await expectTextUncut(occupancy);
        }
        const unknown = page.getByRole("button", { name: "codex / openai / gpt-5-mini", exact: true })
          .getByLabel(locale === "zh-CN" ? "占用未知" : "In use: unknown", { exact: true });
        await expect(unknown).toHaveText("—");
        await expect(unknown.locator('[data-slot="model-occupancy-meter"]')).toHaveCount(0);
        await expectNoSidewaysScroll(page);
      }
      await page.locator('[data-slot="buddy-header"] a[href="#buddies/harness"]').click();
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 1000 });
        const cards = page.locator('[data-slot="harness-list"] [data-slot="harness-card"]');
        expect(await cards.count()).toBeGreaterThan(0);
        for (const card of await cards.all()) {
          const title = (await card.locator("h3").boundingBox())!;
          const groups = card.locator('[data-slot="harness-details"] > [role="group"]');
          await expect(groups).toHaveCount(2);
          await expect(groups.nth(0)).toHaveAccessibleName(locale === "zh-CN" ? "位置" : "Location");
          await expect(groups.nth(1)).toHaveAccessibleName(locale === "zh-CN" ? "账户" : "Account");
          const location = (await groups.nth(0).boundingBox())!, account = (await groups.nth(1).boundingBox())!;
          expect(location.y).toBeGreaterThanOrEqual(title.y + title.height);
          if (width >= 768) {
            expect(Math.abs(location.y - account.y)).toBeLessThanOrEqual(1);
            expect(account.x).toBeGreaterThanOrEqual(location.x + location.width);
          } else {
            expect(account.y).toBeGreaterThanOrEqual(location.y + location.height);
            expect(Math.abs(location.x - account.x)).toBeLessThanOrEqual(1);
          }
          expect(await card.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
        }
        await expectNoSidewaysScroll(page);
      }
    });
  });
}
