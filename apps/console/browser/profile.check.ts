import { expect } from "@playwright/test";
import { expectNoSidewaysScroll, open, test } from "./preview";

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
      await expect(profile.locator("[data-slot=profile-quota]")).toContainText("dsh / deepseek-partner / deepseek-flash / max");
      await expect(profile.getByRole("button", { name: locale === "en-US" ? "Rebuild profile" : "重建画像", exact: true })).toBeDisabled();
      await expect(profile.locator("[data-slot=model-correspondence]")).toContainText(locale === "en-US" ? "Same-name normalization" : "同名归一");
      const modelChoice = profile.getByRole("combobox", { name: /^Model for|对应的模型$/ });
      expect((await modelChoice.boundingBox())!.width).toBeGreaterThanOrEqual(180);
      await profile.scrollIntoViewIfNeeded();
      await expectNoSidewaysScroll(page);
      if (locale === "en-US") expect(await profile.innerText()).not.toMatch(/[\u3400-\u9fff]/);
      await page.getByRole("button", { name: "codex / openai / gpt-5-mini", exact: true }).click();
      await expect(profile).toContainText(locale === "en-US" ? "No profile built yet" : "尚未构建画像");
      await expect(profile.locator("[data-slot=model-correspondence]")).toContainText(locale === "en-US" ? "Profile plane suggestion" : "画像面的建议");
      await expect(profile).toContainText("https://example.org/synthetic-preview/model");
      await expectNoSidewaysScroll(page);
    });
  });
}

test.describe("model profile edits", () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  test("publishes notes by model and stages map, undo and generation with their distinct payloads", async ({ page }) => {
    const publications: Record<string, unknown>[] = [];
    await page.route("**/api/command", async route => {
      const body = route.request().postDataJSON();
      if (body.operation !== "user_policy_publish") return route.fallback();
      publications.push(body.params);
      await route.fulfill({ json: { ok: false, error: { code: "FORBIDDEN", message: "Synthetic save capture" } } });
    });
    await open(page, "#buddies/catalog");
    await page.getByRole("button", { name: CODEX, exact: true }).click();
    const profile = page.locator("[data-slot=model-profile]");
    await profile.getByRole("textbox", { name: "Model note", exact: true }).fill("A shared model note");
    const correspondence = profile.locator("[data-slot=model-correspondence]").first();
    const target = correspondence.getByRole("combobox", { name: /^Model for/ });
    const options = await target.locator("option").evaluateAll(nodes => nodes.map(node => ({ value: (node as HTMLOptionElement).value, text: node.textContent })));
    const other = options.find(option => option.text === "gpt-5-mini")!;
    await target.selectOption(other.value);
    await correspondence.getByRole("button", { name: "Stage change", exact: true }).click();
    await expect(correspondence).toContainText("Will map to gpt-5-mini when saved");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => publications.length).toBe(1);
    expect(publications[0]).toMatchObject({ modelNoteChanges: [{ modelId: expect.any(String), text: "A shared model note" }], modelCorrespondenceChanges: [{ harness: "codex", provider: "openai", model: "gpt-5-codex", action: "map", modelId: other.value }] });
    expect(publications[0]).not.toHaveProperty("modelServiceNoteChanges");
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
    expect(publications[2]).toMatchObject({ modelCorrespondenceChanges: [{ action: "undo" }] });
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
