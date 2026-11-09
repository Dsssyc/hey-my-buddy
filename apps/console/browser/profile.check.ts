import { expect } from "@playwright/test";
import { join } from "node:path";
import { expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

const CODEX = "codex / openai / gpt-5-codex";
for (const locale of ["en-US", "zh-CN"]) for (const width of [1440, 390]) for (const theme of ["light", "dark"] as const) {
  test.describe(`model profiles ${locale} ${width} ${theme}`, () => {
    test.use({ locale, viewport: { width, height: 1000 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    });
    test("shows sourced claims, usage, empty profiles and correspondence provenance without overflow", async ({ page }) => {
      await open(page, "#buddies/catalog");
      await page.getByRole("button", { name: CODEX, exact: true }).click();
      const profile = page.locator("[data-slot=model-profile]");
      await expect(profile).toContainText("Handles multi-file code changes.");
      await expect(profile).toContainText(locale === "en-US" ? "Vendor claim" : "厂商自述");
      await expect(profile).toContainText(locale === "en-US" ? "Third party" : "第三方");
      await expect(profile).toContainText("2026-09-20");
      await expect(profile).toContainText("https://example.org/synthetic-preview/coding");
      await expect(profile.locator("a")).toHaveCount(0);
      await expect(profile.locator("[data-slot=profile-run]")).toContainText("4,200");
      const tools = profile.locator("[data-slot=profile-tool-summary]");
      await expect(tools.getByRole("listitem")).toHaveCount(3);
      await expect(tools.getByRole("listitem").nth(0)).toContainText(locale === "en-US" ? "web_searchTool calls2Failed0Unknown1" : "web_search工具调用2失败0未知1");
      await expect(tools.getByRole("listitem").nth(1)).toContainText(locale === "en-US" ? "web_fetchTool calls2Failed2Unknown0" : "web_fetch工具调用2失败2未知0");
      await expect(tools.getByRole("listitem").nth(2)).toContainText(locale === "en-US" ? "Unknown tool" : "未知工具");
      await expect(tools.locator(".text-fail")).toHaveCount(1);
      await expect(tools.getByRole("listitem").nth(1).locator("dd.text-fail")).toHaveText("2");
      for (const row of await tools.getByRole("listitem").all()) {
        await expectWholeAndApart(row, row.locator(":scope > span, :scope > dl"));
        await expectWholeAndApart(row.locator("dl"), row.locator("dl > div"));
        await expectTextUncut(row.locator("span, dt, dd"));
      }
      await expect(profile.locator("[data-slot=profile-quota]")).toContainText("dsh / deepseek-partner / deepseek-flash / max");
      await expect(profile.getByRole("button", { name: locale === "en-US" ? "Rebuild profile" : "重建画像", exact: true })).toBeDisabled();
      await expect(profile.locator("[data-slot=model-correspondence]")).toContainText(locale === "en-US" ? "Same-name normalization" : "同名归一");
      const modelChoice = profile.getByRole("combobox", { name: /^Model for|对应的模型$/ });
      expect((await modelChoice.boundingBox())!.width).toBeGreaterThanOrEqual(180);
      await profile.scrollIntoViewIfNeeded();
      await expectNoSidewaysScroll(page);
      if (locale === "en-US") expect(await profile.innerText()).not.toMatch(/[\u3400-\u9fff]/);
      if (process.env.HMB_PROFILE_SHOTS) {
        await tools.scrollIntoViewIfNeeded();
        await page.screenshot({ path: join(process.env.HMB_PROFILE_SHOTS, `tools-${locale}-${theme}-${width}.png`) });
      }
      await page.getByRole("button", { name: "codex / openai / gpt-5-mini", exact: true }).click();
      await expect(profile).toContainText(locale === "en-US" ? "No profile built yet" : "尚未构建画像");
      await expect(profile.locator("[data-slot=model-correspondence]")).toContainText(locale === "en-US" ? "Profile plane suggestion" : "画像面的建议");
      await expect(profile).toContainText("https://example.org/synthetic-preview/model");
      await expectNoSidewaysScroll(page);
      await page.getByRole("button", { name: "zcode / zai / glm-5", exact: true }).click();
      await expect(tools).toHaveText(locale === "en-US" ? "Tool call outcomes are unknown" : "工具调用的成败未知");
      await expectNoSidewaysScroll(page);
      if (process.env.HMB_PROFILE_SHOTS) {
        await tools.scrollIntoViewIfNeeded();
        await page.screenshot({ path: join(process.env.HMB_PROFILE_SHOTS, `unknown-${locale}-${theme}-${width}.png`) });
      }
    });
  });
}

test.describe("model profile edits", () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  test("stages map, undo and generation with their distinct payloads", async ({ page }) => {
    const publications: Record<string, unknown>[] = [];
    let undoId = "";
    await page.route("**/api/console", async route => {
      const response = await route.fetch();
      const snapshot = await response.json();
      undoId = snapshot.modelCorrespondences.find((row: { model: string }) => row.model === "gpt-5-mini").correspondenceId;
      await route.fulfill({ response, json: snapshot });
    });
    await page.route("**/api/command", async route => {
      const body = route.request().postDataJSON();
      if (body.operation !== "user_policy_publish") return route.fallback();
      publications.push(body.params);
      await route.fulfill({ json: { ok: false, error: { code: "FORBIDDEN", message: "Synthetic save capture" } } });
    });
    await open(page, "#buddies/catalog");
    await page.getByRole("button", { name: CODEX, exact: true }).click();
    const profile = page.locator("[data-slot=model-profile]");
    const correspondence = profile.locator("[data-slot=model-correspondence]").first();
    const target = correspondence.getByRole("combobox", { name: /^Model for/ });
    const options = await target.locator("option").evaluateAll(nodes => nodes.map(node => ({ value: (node as HTMLOptionElement).value, text: node.textContent })));
    const other = options.find(option => option.text === "gpt-5-mini")!;
    await target.selectOption(other.value);
    await correspondence.getByRole("button", { name: "Stage change", exact: true }).click();
    await expect(correspondence).toContainText("Will map to gpt-5-mini when saved");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => publications.length).toBe(1);
    expect(publications[0]).toMatchObject({ modelCorrespondenceChanges: [{ harness: "codex", provider: "openai", model: "gpt-5-codex", action: "map", modelId: other.value }] });
    // Reload discards the deliberately refused synthetic save and starts another draft.
    await page.reload();
    await expect(profile).toBeVisible();
    await profile.getByRole("combobox", { name: /^Correspondence action/ }).selectOption("generation");
    await profile.getByRole("textbox", { name: /^New generation display name/ }).fill("Codex next generation");
    await profile.getByRole("button", { name: "Stage change", exact: true }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => publications.length).toBe(2);
    expect(publications[1]).toMatchObject({ modelCorrespondenceChanges: [{ action: "generation", displayName: "Codex next generation" }] });
    await page.reload();
    await page.getByRole("button", { name: "codex / openai / gpt-5-mini", exact: true }).click();
    await profile.getByRole("button", { name: "Undo latest change", exact: true }).click();
    await expect(profile).toContainText("Will restore the previous correspondence when saved");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => publications.length).toBe(3);
    expect(undoId).not.toBe("");
    expect(publications[2]).toMatchObject({ modelCorrespondenceChanges: [{ harness: "codex", provider: "openai", model: "gpt-5-mini", action: "undo", correspondenceId: undoId }] });
  });
  test("requires an explicit build after showing the configured buddy and its quota", async ({ page }) => {
    const commands: Record<string, unknown>[] = [];
    await page.route("**/api/console", async route => {
      const response = await route.fetch();
      const snapshot = await response.json();
      snapshot.configuration.profileUnavailableReason = null;
      snapshot.configuration.profileBuddyId = "codex:openai:gpt-5-codex:high";
      await route.fulfill({ response, json: snapshot });
    });
    await page.route("**/api/command", async route => {
      const body = route.request().postDataJSON();
      if (body.operation !== "profile_start") return route.fallback();
      commands.push(body.params);
      await route.fulfill({ json: { ok: true, result: { status: "running", profileRunId: "synthetic-profile", runId: "synthetic-task", buddyId: "codex:openai:gpt-5-codex:high", usesQuota: true } } });
    });
    await open(page, "#buddies/catalog");
    await page.getByRole("button", { name: CODEX, exact: true }).click();
    const profile = page.locator("[data-slot=model-profile]");
    await expect(profile.locator("[data-slot=profile-quota]")).toContainText("codex / openai / gpt-5-codex / high will browse the web");
    expect(commands).toEqual([]);
    await profile.getByRole("button", { name: "Rebuild profile", exact: true }).click();
    await expect(profile.getByRole("status")).toContainText("The profile is running with codex / openai / gpt-5-codex / high");
    expect(commands).toEqual([{ modelId: expect.any(String) }]);
  });
});

for (const locale of ["en-US", "zh-CN"]) test.describe(`profile tool outcomes and undo conflict ${locale}`, () => {
  test.use({ locale, viewport: { width: 390, height: 1000 }, reducedMotion: "reduce" });
  test("omits empty summaries and keeps partial failures uncoloured", async ({ page }) => {
    let toolSummary: { toolName: string; calls: number; failed: number; unknown: number }[] = [];
    await page.route("**/api/console", async route => {
      const response = await route.fetch();
      const snapshot = await response.json();
      for (const model of snapshot.models) if (model.latestProfileRun) model.latestProfileRun.toolSummary = toolSummary;
      await route.fulfill({ response, json: snapshot });
    });
    await open(page, "#buddies/catalog");
    await page.getByRole("button", { name: CODEX, exact: true }).click();
    await expect(page.locator("[data-slot=profile-run]")).toBeVisible();
    await expect(page.locator("[data-slot=profile-tool-summary]")).toHaveCount(0);
    toolSummary = [{ toolName: "web_fetch", calls: 3, failed: 1, unknown: 1 }];
    await page.reload();
    const tools = page.locator("[data-slot=profile-tool-summary]");
    await expect(tools).toContainText(locale === "en-US" ? "web_fetchTool calls3Failed1Unknown1" : "web_fetch工具调用3失败1未知1");
    await expect(tools.locator(".text-fail")).toHaveCount(0);
  });
  test("shows the localized refresh instruction when the displayed correspondence is stale", async ({ page }) => {
    const writes: unknown[] = [];
    await page.route("**/api/command", async route => {
      const body = route.request().postDataJSON();
      if (body.operation !== "user_policy_publish") return route.fallback();
      writes.push(body.params);
      await route.fulfill({ json: { ok: false, error: { code: "CONFLICT", message: "Synthetic stale correspondence" } } });
    });
    await open(page, "#buddies/catalog");
    await page.getByRole("button", { name: "codex / openai / gpt-5-mini", exact: true }).click();
    const profile = page.locator("[data-slot=model-profile]");
    await profile.getByRole("button", { name: locale === "en-US" ? "Undo latest change" : "撤回最近一次改动", exact: true }).click();
    await page.getByRole("button", { name: locale === "en-US" ? "Save" : "保存", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveText(locale === "en-US"
      ? "This correspondence has changed; refresh and undo again." : "这条对应关系已经变了，请刷新后再撤回。");
    await expect(profile).toContainText(locale === "en-US" ? "Will restore the previous correspondence when saved" : "保存后恢复上一次对应关系");
    expect(writes).toHaveLength(1);
    await expectNoSidewaysScroll(page);
  });
});
