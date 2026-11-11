import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect } from "@playwright/test";
import type { Snapshot } from "../src/types";
import { expectNoSidewaysScroll, expectWholeAndApart, open, test } from "./preview";

test.use({ headless: true });

const captures = fileURLToPath(new URL("../../../tmp/alias-fixes/captures", import.meta.url));
const model = "codex / openai / gpt-5-codex";
for (const locale of ["zh-CN", "en-US"]) for (const theme of ["dark", "light"]) for (const width of [1440, 390]) {
  test.describe(`saved aliases ${locale} ${theme} ${width}`, () => {
    test.use({ locale, viewport: { width, height: 1000 }, reducedMotion: "reduce" });
    test("keeps draft aliases in the input and fits saved tags in a stable effort column", async ({ page }) => {
      const zh = locale === "zh-CN";
      const short = zh ? "日常审查专用工作伙伴" : "Reviewdesk";
      const long = zh ? "日常审查专用工作伙伴".repeat(4) : "Reviewdesk".repeat(4);
      let alias: string | null = null;
      let targetId = "";
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await page.route("**/api/console", async route => {
        const response = await route.fetch();
        const snapshot: Snapshot = await response.json();
        const buddy = snapshot.buddies.find(buddy => buddy.model === "gpt-5-codex" && buddy.effort === "medium")!;
        targetId = buddy.buddyId;
        buddy.alias = alias;
        buddy.enabled = true;
        snapshot.preferences = snapshot.preferences.filter(entry => entry.buddyId !== targetId)
          .concat([{ buddyId: targetId, mode: "exclude", note: null }]);
        snapshot.configuration!.routerBuddyIds = [targetId];
        await route.fulfill({ response, json: snapshot });
      });
      await page.route("**/api/command", async route => {
        const body = route.request().postDataJSON();
        if (body.operation !== "user_policy_publish") { await route.fallback(); return; }
        alias = body.params.buddySettings[0].alias;
        await route.fulfill({ json: { ok: true, result: { published: true } } });
      });
      await open(page, "#buddies/catalog");
      await page.getByRole("button", { name: model, exact: true }).click();
      const drawer = page.locator('[data-slot=model-drawer]');
      await drawer.locator('[data-slot=buddy-stop][aria-label=medium]').click();
      const input = drawer.locator('[data-slot=buddy-alias] input');
      const list = drawer.locator('[data-slot=buddy-list]');
      const selected = drawer.locator('[data-slot=buddy-stop][aria-pressed=true]');
      const row = page.locator('[data-slot=model-row]').filter({ has: page.getByRole("button", { name: model, exact: true }) });
      const tag = selected.locator('[data-slot=alias-tag]');
      const save = page.getByRole("button", { name: zh ? "保存" : "Save", exact: true });
      await expect(input).toBeVisible();
      const originalWidth = (await list.boundingBox())!.width;
      if (width >= 900) expect(originalWidth).toBe(352);
      const stable = async () => {
        expect((await list.boundingBox())!.width).toBe(originalWidth);
        await expectWholeAndApart(selected, selected.locator(":scope > *"));
        expect(await list.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
        expect(await drawer.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
        expect(await drawer.locator('[data-slot=buddy-list-viewport]').evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
        await expectNoSidewaysScroll(page);
      };
      const capture = async (kind: string) => {
        if (width !== 1440) return;
        await mkdir(captures, { recursive: true });
        await page.screenshot({ path: `${captures}/drawer-${locale}-${theme}-${kind}.png` });
      };
      await expect(tag).toHaveCount(0);
      await stable();
      await capture("none");
      for (const [kind, value] of [["short", short], ["long", long]]) {
        await input.fill(value);
        await expect(input).toHaveValue(value);
        await expect(selected.locator('[data-slot=unsaved-mark]')).toHaveCount(1);
        await expect(drawer.locator('[data-slot=buddy-alias]').locator('..').locator('[data-slot=unsaved-mark]')).toHaveCount(1);
        if (kind === "short") {
          await expect(tag).toHaveCount(0);
          await expect(row.locator('[data-slot=alias-tag]')).toHaveCount(0);
        } else {
          await expect(tag).toHaveText(short);
          await expect(row.locator('[data-slot=alias-tag]')).toHaveText(short);
        }
        await stable();
        await page.getByRole("link", { name: "Router", exact: true }).click();
        await expect(page.locator('[data-slot=routing-routers-region] tbody tr')).not.toHaveAttribute("aria-label", new RegExp(value));
        const routerTags = page.locator('[data-slot=routing-routers-region] [data-slot=alias-tag]');
        if (kind === "short") await expect(routerTags).toHaveCount(0);
        else await expect(routerTags).toHaveText(short);
        await page.getByRole("link", { name: zh ? "目录" : "Catalog", exact: true }).click();
        await save.click();
        await expect(save).toHaveCount(0);
        await expect(tag).toHaveText(value);
        await expect(row.locator('[data-slot=alias-tag]')).toHaveText(value);
        await expect(input).toHaveValue(value);
        await stable();
        const directoryTag = row.locator('[data-slot=alias-tag]');
        expect(await directoryTag.evaluate(node => node.getBoundingClientRect().width)).toBeLessThanOrEqual(136);
        expect(await directoryTag.locator(":scope > span").evaluate(node => node.scrollWidth > node.clientWidth)).toBe(kind === "long");
        const truncation = await tag.locator(":scope > span").evaluate(node => ({
          ellipsis: getComputedStyle(node).textOverflow,
          overflow: getComputedStyle(node).overflow,
          clipped: node.scrollWidth > node.clientWidth,
        }));
        expect(truncation.clipped).toBe(kind === "long");
        if (kind === "long") expect(truncation).toEqual({ ellipsis: "ellipsis", overflow: "hidden", clipped: true });
        await capture(kind);
      }
    });
  });
}
