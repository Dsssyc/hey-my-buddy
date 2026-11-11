import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect } from "@playwright/test";
import type { Snapshot } from "../src/types";
import { open, settle, test, expectNoSidewaysScroll } from "./preview";

test.use({ headless: true });

const model = "codex / openai / gpt-5-codex";
for (const locale of ["zh-CN", "en-US"]) for (const theme of ["light", "dark"]) for (const width of [1440, 390]) {
  test.describe(`aliases ${locale} ${theme} ${width}`, () => {
    test.use({ locale, viewport: { width, height: 1000 }, reducedMotion: "reduce" });
    test("sets, rejects and clears the alias with matching tokens and aligned fields", async ({ page }) => {
      const zh = locale === "zh-CN";
      const aliasName = zh ? "别名" : "Alias";
      const saveName = zh ? "保存" : "Save";
      let alias: string | null = null;
      let targetId = "";
      const published: unknown[] = [];
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await page.route("**/api/console", async route => {
        const response = await route.fetch();
        const snapshot = await response.json() as Snapshot;
        const buddy = snapshot.buddies.find(b => b.model === "gpt-5-codex" && b.effort === "high")!;
        targetId = buddy.buddyId;
        buddy.alias = alias;
        snapshot.configuration!.routerBuddyIds = [targetId];
        await route.fulfill({ response, json: snapshot });
      });
      await page.route("**/api/command", async route => {
        const body = route.request().postDataJSON();
        if (body.operation !== "user_policy_publish") { await route.fallback(); return; }
        const patch = body.params.buddySettings[0];
        published.push(patch);
        if (patch.alias === "duplicate") {
          await route.fulfill({ status: 200, json: { ok: false, error: { code: "INVALID_ARGUMENT", message: "refused", details: {
            buddyId: targetId, conflictingBuddyId: "opaque:hashed-id", reason: { code: "alias.conflict", params: { conflictingBuddy: { harness: "claude", provider: "anthropic", model: "claude-opus-5-5", effort: "high" } } },
          } } } });
        } else {
          alias = patch.alias;
          await route.fulfill({ json: { ok: true, result: { published: true } } });
        }
      });
      await open(page, "#buddies/catalog");
      await page.getByRole("button", { name: model, exact: true }).click();
      const drawer = page.locator('[data-slot=model-drawer]');
      const field = drawer.getByRole("textbox", { name: aliasName, exact: true });
      await expect(field).toBeVisible();
      await expect(page.locator('[data-slot=alias-tag]')).toHaveCount(0);
      const geometry = await drawer.locator('[data-slot=buddy-panel]').evaluate(panel => {
        const preference = panel.querySelector('[role=radiogroup]')!;
        const field = panel.querySelector('[data-slot=buddy-alias] input')!;
        const note = panel.querySelector('input:not([id$="-alias"])')!;
        const box = (node: Element) => { const b = node.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height }; };
        const labels = [...panel.querySelectorAll('label')].map(box);
        const style = (node: Element) => { const s = getComputedStyle(node); return [s.backgroundColor, s.borderRadius, s.borderWidth, s.fontSize]; };
        return { preference: box(preference), field: box(field), note: box(note), labels, aliasStyle: style(field), noteStyle: style(note) };
      });
      expect(geometry.field.height).toBe(geometry.note.height);
      expect(geometry.field.height).toBe(32);
      expect(Math.abs(geometry.preference.width - geometry.field.width)).toBeLessThanOrEqual(1);
      expect(geometry.field.x).toBe(geometry.preference.x);
      expect(geometry.field.y).toBeGreaterThan(geometry.preference.y);
      expect(geometry.field.y).toBeLessThan(geometry.note.y);
      expect(new Set(geometry.labels.map(label => label.x)).size).toBe(1);
      expect(geometry.aliasStyle).toEqual(geometry.noteStyle);
      await field.fill("Review desk");
      await page.getByRole("button", { name: saveName, exact: true }).click();
      await expect.poll(() => alias).toBe("Review desk");
      await expect(page.getByRole("button", { name: saveName, exact: true })).toHaveCount(0);
      expect(published[0]).toEqual({ buddyId: targetId, alias: "Review desk" });
      const tag = drawer.locator('[data-slot=buddy-stop][aria-pressed=true] [data-slot=alias-tag]');
      await expect(tag).toHaveText("Review desk");
      const colors = await tag.evaluate(node => {
        const actual = getComputedStyle(node);
        const tokens = getComputedStyle(document.documentElement);
        return { ink: actual.color, line: actual.borderColor, wash: actual.backgroundColor,
          tokenInk: tokens.getPropertyValue("--alias").trim(), tokenLine: tokens.getPropertyValue("--alias-line").trim(), tokenWash: tokens.getPropertyValue("--alias-wash").trim(),
          height: node.getBoundingClientRect().height, radius: actual.borderRadius, margin: actual.marginLeft, size: actual.fontSize };
      });
      const rgb = (hex: string) => `rgb(${hex.slice(1).match(/../g)!.map(part => parseInt(part, 16)).join(", ")})`;
      expect([colors.ink, colors.line, colors.wash]).toEqual([colors.tokenInk, colors.tokenLine, colors.tokenWash].map(rgb));
      expect([colors.tokenInk, colors.tokenLine, colors.tokenWash]).toEqual(theme === "light" ? ["#104aee", "#104aee", "#dee6fd"] : ["#5593e6", "#5593e6", "#1f2837"]);
      expect([colors.height, colors.radius, colors.margin, colors.size]).toEqual([20, "4px", "8px", "12px"]);
      await expect(page.locator('[data-slot=model-row] [data-slot=alias-tag]')).toHaveText("Review desk");
      await page.getByRole("searchbox").fill("review desk");
      await expect(page.getByRole("button", { name: model, exact: true })).toBeVisible();
      await page.getByRole("searchbox").fill("");
      await page.getByRole("link", { name: "Router", exact: true }).click();
      await expect(page.locator('[data-slot=routing-routers-region] [data-slot=alias-tag]')).toHaveText("Review desk");
      await page.getByRole("link", { name: zh ? "目录" : "Catalog", exact: true }).click();
      const modelButton = page.getByRole("button", { name: model, exact: true });
      if (await modelButton.getAttribute("aria-expanded") === "false") await modelButton.click();
      await field.fill("duplicate");
      await page.getByRole("button", { name: saveName, exact: true }).click();
      const error = drawer.locator('[data-slot=alias-error]');
      await expect(error).toHaveText(zh ? "这个别名已被 claude / anthropic / claude-opus-5-5 / high 使用。" : "This alias is already used by claude / anthropic / claude-opus-5-5 / high.");
      const errorBox = (await error.boundingBox())!;
      const fieldBox = (await field.boundingBox())!;
      const noteBox = (await drawer.getByRole("textbox", { name: zh ? "high 的偏好理由" : "Reason for the preference of high", exact: true }).boundingBox())!;
      expect(errorBox.y).toBeGreaterThan(fieldBox.y + fieldBox.height);
      expect(errorBox.x).toBe(fieldBox.x);
      expect(Math.abs(errorBox.width - noteBox.width)).toBeLessThanOrEqual(1);
      const words = error.locator('[data-slot=buddy-name] > .whitespace-nowrap');
      await expect(words).toHaveCount(4);
      expect(await words.evaluateAll(nodes => nodes.every(node => {
        const range = document.createRange(); range.selectNodeContents(node);
        return new Set([...range.getClientRects()].filter(rect => rect.width > 0).map(rect => rect.y)).size === 1;
      }))).toBe(true);
      await expect(error).not.toContainText("[");
      await expect(error).not.toContainText("]");
      await expect(field).toHaveAttribute("aria-invalid", "true");
      await expect(field).toHaveValue("duplicate");
      await expect(tag).toHaveText("Review desk");
      await expect(page.locator('[data-slot=model-row] [data-slot=alias-tag]')).toHaveText("Review desk");
      if (width === 1440) {
        const captures = fileURLToPath(new URL("../../../tmp/alias-console/captures", import.meta.url));
        await mkdir(captures, { recursive: true });
        await page.screenshot({ path: `${captures}/drawer-${locale}-${theme}.png` });
      }
      await field.fill("");
      await expect(error).toHaveCount(0);
      await page.getByRole("button", { name: saveName, exact: true }).click();
      await expect.poll(() => alias).toBeNull();
      await expect(page.locator('[data-slot=alias-tag]')).toHaveCount(0);
      await settle(page);
      await expectNoSidewaysScroll(page);
    });
  });
}
