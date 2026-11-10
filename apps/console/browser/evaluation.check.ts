import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { expect } from "@playwright/test";
import { expectNoSidewaysScroll, open, test } from "./preview";

const CODEX_ID = "00000000-0000-4000-8000-000000000003";
const MINI_ID = "00000000-0000-4000-8000-000000000004";
const GLM_ID = "00000000-0000-4000-8000-000000000005";
const CODEX_HIGH = `#buddies/evaluation/codex/${CODEX_ID}/high`;

for (const locale of ["zh-CN", "en-US"]) for (const theme of ["light", "dark"] as const) {
  test.describe(`evaluation ${locale} ${theme} 1440px`, () => {
    test.use({ locale, viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme));

    test("shows the kind list, domain evidence, previous samples and model portrait", async ({ page }) => {
      await open(page, CODEX_HIGH);
      const list = page.locator('[data-slot="evaluation-list"]');
      const detail = page.locator('[data-slot="evaluation-detail"]');
      await expect(list).toBeVisible();
      await expect(detail).toBeVisible();
      await expect(detail).toContainText("codex / gpt-5-codex / high");
      await expect(detail.locator('[data-slot="evaluation-names"]')).toContainText("gpt-5-codex");
      await expect(detail.locator('[data-slot="evaluation-tally"]').first()).toContainText("1");
      const tally = detail.locator('[data-slot="evaluation-tally"]').first();
      const sampleBar = tally.locator('[data-slot="quant-bar-trigger"]');
      await expect(sampleBar).toHaveAttribute("aria-expanded", "false");
      await expect(page.locator('[data-slot="quant-bar-detail"]')).toHaveCount(0);
      await sampleBar.click();
      await expect(sampleBar).toHaveAttribute("aria-expanded", "true");
      await expect(page.locator('[data-slot="quant-bar-detail"]')).toContainText(locale === "zh-CN" ? "中位数" : "Medians");
      await sampleBar.press("Space");
      await expect(sampleBar).toHaveAttribute("aria-expanded", "false");
      await expect(detail).toContainText("Earlier Codex");
      await expect(detail).toContainText(locale === "zh-CN" ? "之前的样本" : "Previous samples");
      const previousBar = detail.locator('[data-slot="evaluation-tally"]').nth(1).locator('[data-slot="quant-bar-trigger"]');
      await expect(previousBar).toHaveAttribute("aria-expanded", "false");
      await previousBar.click();
      await expect(page.locator('[data-slot="quant-bar-detail"]')).toBeVisible();
      await previousBar.click();
      await expect(detail).toContainText("合成样例：只有样式与排版这个节点有样本。");
      await expect(detail).not.toContainText(locale === "zh-CN" ? "引用的微任务" : "Cited micro tasks");
      const matrix = detail.getByRole("table");
      await expect(matrix.getByRole("rowheader", { name: /界面工程/ })).toBeVisible();
      await expect(matrix.getByRole("rowheader", { name: /样式与排版/ })).toBeVisible();
      await expect(matrix.getByRole("rowheader", { name: /路由判断|文档与技能|共享技能/ })).toHaveCount(0);
      await expectNoSidewaysScroll(page);
      const destination = process.env.HMB_EVALUATION_SHOTS;
      if (destination) { await mkdir(destination, { recursive: true }); await page.screenshot({ path: join(destination, `evaluation-${locale}-${theme}-1440.png`), fullPage: true }); }
      await detail.getByRole("link", { name: locale === "zh-CN" ? "模型画像" : "Model profile" }).click();
      await expect(page).toHaveURL(new RegExp(`${CODEX_HIGH}/profile$`));
      const portrait = detail.locator('[data-slot="model-portrait"]');
      await expect(portrait).toContainText("Handles multi-file code changes.");
      await expect(portrait).toContainText("2026-09-20");
      await expect(portrait).toContainText(locale === "zh-CN" ? "厂商自述" : "Vendor claim");
      await expectNoSidewaysScroll(page);
    });

    test("keeps the catalog drawer scoped to service and buddy facts and links sample counts", async ({ page }) => {
      await open(page, "#buddies/catalog/buddy/codex%3Aopenai%3Agpt-5-codex%3Ahigh");
      const drawer = page.locator('[data-slot="model-drawer"]');
      await expect(drawer.locator('[data-slot="model-profile"], [data-slot="kind-evaluation"], [data-slot="model-correspondence"]')).toHaveCount(0);
      await drawer.locator('[data-slot="buddy-samples"] a').click();
      await expect(page).toHaveURL(new RegExp(`${CODEX_HIGH}$`));
      await expect(page.locator('[data-slot="evaluation-detail"]')).toBeVisible();
    });
  });
}

test.describe("evaluation actions", () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });

  test("stages generation, mapping and undo only after an explanation", async ({ page }) => {
    const publications: Record<string, unknown>[] = [];
    await page.route("**/api/command", async route => {
      const body = route.request().postDataJSON();
      if (body.operation !== "user_policy_publish") return route.fallback();
      publications.push(body.params);
      await route.fulfill({ json: { ok: false, error: { code: "FORBIDDEN", message: "Synthetic save capture" } } });
    });
    await open(page, CODEX_HIGH);
    const names = page.locator('[data-slot="evaluation-names"]');
    await names.getByRole("button", { name: "Model actions" }).click();
    await expect(page.getByRole("menuitem")).toHaveCount(3);
    await expect(page.getByRole("menuitem", { name: "Undo" })).toBeDisabled();
    await page.getByRole("menuitem", { name: "Mark a new generation" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("gpt-5-codex");
    await expect(dialog).toContainText("previous samples");
    await expect(dialog.getByRole("radio")).toHaveCount(0);
    await dialog.getByRole("button", { name: "Stage change" }).click();
    await expect(names).toContainText("gpt-5-codex");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => publications.length).toBe(1);
    expect(publications[0]).toMatchObject({ modelCorrespondenceChanges: [{ harness: "codex", provider: "openai", model: "gpt-5-codex", action: "generation" }] });
    await page.reload();
    await names.getByRole("button", { name: "Model actions" }).click();
    await page.getByRole("menuitem", { name: "Map to another model" }).click();
    await dialog.getByRole("combobox").selectOption(MINI_ID);
    await dialog.getByRole("button", { name: "Stage change" }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => publications.length).toBe(2);
    expect(publications[1]).toMatchObject({ modelCorrespondenceChanges: [{ action: "map", modelId: MINI_ID }] });
    await page.reload();
    await page.goto(`#buddies/evaluation/codex/${MINI_ID}/high`);
    await names.getByRole("button", { name: "Model actions" }).click();
    await page.getByRole("menuitem", { name: "Undo" }).click();
    await expect(dialog).toContainText("gpt-5-mini");
    await dialog.getByRole("button", { name: "Stage change" }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => publications.length).toBe(3);
    expect(publications[2]).toMatchObject({ modelCorrespondenceChanges: [{ action: "undo", correspondenceId: expect.any(String) }] });
  });

  test("disables actions when Router work is unconfigured", async ({ page }) => {
    await page.route("**/api/console", async route => {
      const response = await route.fetch();
      const snapshot = await response.json();
      snapshot.configuration.profileBuddyId = null;
      snapshot.configuration.maintenanceBuddyId = null;
      await route.fulfill({ response, json: snapshot });
    });
    await open(page, CODEX_HIGH);
    const list = page.locator('[data-slot="evaluation-list"]');
    await expect(list.getByRole("button", { name: "Build profiles" })).toBeDisabled();
    await expect(list.getByRole("button", { name: "Maintain now" })).toBeDisabled();
    await list.getByRole("button", { name: "Build profiles" }).locator("..").hover();
    await expect(page.getByRole("tooltip")).toContainText("Choose a buddy in Router Profiles first.");
    await list.getByRole("button", { name: "Maintain now" }).locator("..").hover();
    await expect(page.getByRole("tooltip")).toContainText("Choose a buddy in Router Maintenance first.");
    await page.locator('[data-slot="evaluation-detail"]').getByRole("link", { name: "Model profile" }).click();
    await expect(page.locator('[data-slot="model-portrait"]').getByRole("button", { name: "Rebuild profile" })).toBeDisabled();
  });

  test("keeps a staged undo visible when its correspondence became stale", async ({ page }) => {
    const writes: unknown[] = [];
    await page.route("**/api/command", async route => {
      const body = route.request().postDataJSON();
      if (body.operation !== "user_policy_publish") return route.fallback();
      writes.push(body.params);
      await route.fulfill({ json: { ok: false, error: { code: "CONFLICT", message: "Synthetic stale correspondence" } } });
    });
    await page.goto(`#buddies/evaluation/codex/${MINI_ID}/high`);
    const names = page.locator('[data-slot="evaluation-names"]');
    await names.getByRole("button", { name: "Model actions" }).click();
    await page.getByRole("menuitem", { name: "Undo" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Stage change" }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveText("The correspondence has changed; refresh before undoing.");
    await expect(names).toContainText("Will restore the previous correspondence when saved");
    expect(writes).toHaveLength(1);
  });

  test("confirms batch profile builds before sending one command per model", async ({ page }) => {
    const starts: string[] = [];
    await page.route("**/api/console", async route => {
      const response = await route.fetch();
      const snapshot = await response.json();
      snapshot.configuration.profileUnavailableReason = null;
      await route.fulfill({ response, json: snapshot });
    });
    await page.route("**/api/command", async route => {
      const body = route.request().postDataJSON();
      if (body.operation !== "profile_start") return route.fallback();
      starts.push(body.params.modelId);
      await route.fulfill({ json: { ok: true, result: { status: "unavailable", reason: { code: "profile-web-unsupported", params: {} } } } });
    });
    await open(page, CODEX_HIGH);
    await page.locator('[data-slot="evaluation-list"]').getByRole("button", { name: "Build profiles" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("dsh / deepseek-partner / deepseek-flash / max");
    await expect(dialog).toContainText("glm-5");
    expect(starts).toEqual([]);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    expect(starts).toEqual([]);
    await page.locator('[data-slot="evaluation-list"]').getByRole("button", { name: "Build profiles" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Confirm build" }).click();
    await expect.poll(() => starts.length).toBeGreaterThan(1);
    expect(new Set(starts).size).toBe(starts.length);
  });

  test("shows a short failure notice linking to its exact Router profile record", async ({ page }) => {
    let started = false;
    await page.route("**/api/console", async route => {
      const response = await route.fetch();
      const snapshot = await response.json();
      snapshot.configuration.profileUnavailableReason = null;
      if (started) {
        const model = snapshot.models.find((row: { modelId: string }) => row.modelId === GLM_ID);
        model.latestProfileRun = { ...model.latestProfileRun, profileRunId: "synthetic-profile-failure-new", state: "failed" };
      }
      await route.fulfill({ response, json: snapshot });
    });
    await page.route("**/api/command", async route => {
      const body = route.request().postDataJSON();
      if (body.operation !== "profile_start") return route.fallback();
      started = true;
      await route.fulfill({ json: { ok: true, result: { status: "running", profileRunId: "synthetic-profile-failure-new", runId: "synthetic-profile-task-failed", buddyId: "dsh:deepseek-partner:deepseek-flash:max", usesQuota: true } } });
    });
    await page.clock.install();
    await open(page, `#buddies/evaluation/zcode/${GLM_ID}/high/profile`);
    await page.locator('[data-slot="model-portrait"]').getByRole("button", { name: "Build profile" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Confirm build" }).click();
    const notice = page.getByRole("status").filter({ hasText: "glm-5" });
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("answer.claims[0].sourceUrl");
    await notice.locator('[data-slot="router-usage"] [data-slot="quant-bar-trigger"]').click();
    await expect(page.locator('[data-slot="quant-bar-detail"]')).toContainText("6,144");
    await expect(notice.getByRole("link", { name: "View run record" })).toHaveAttribute("href", "#buddies/router/profiles/synthetic-profile-failure-new");
    await page.clock.runFor(6100);
    await expect(notice).toHaveCount(0);
  });
});
