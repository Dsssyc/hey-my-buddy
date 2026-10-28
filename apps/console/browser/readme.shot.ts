import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { expect } from "@playwright/test";
import { test, open, settle, expectNoSidewaysScroll } from "./preview";

const repository = fileURLToPath(new URL("../../..", import.meta.url));
const output = fileURLToPath(new URL("../../../docs/assets/", import.meta.url));
const macro = "macro-11111111-1111-4111-8111-111111111111";

for (const language of ["en", "zh"] as const) {
  test.describe(`README ${language}`, () => {
    let server: ChildProcess;
    let baseURL: string;
    test.use({ locale: language === "zh" ? "zh-CN" : "en-US" });
    test.beforeAll(async () => {
      server = spawn(process.env.HMB_PREVIEW_PYTHON ?? "python3", [
        "tests/probes/macro_task_console_preview.py", "--scenario", "readme", "--language", language,
        "--assets", `${repository}/tmp/readme-shot/assets`,
      ], { cwd: repository, stdio: ["ignore", "pipe", "pipe"] });
      baseURL = await new Promise<string>((resolve, reject) => {
        let banner = "";
        let stderr = "";
        server.stderr!.on("data", data => { stderr += data; });
        server.once("error", reject);
        server.once("exit", code => reject(new Error(`Preview exited ${code}: ${stderr}`)));
        server.stdout!.on("data", data => {
          banner += data;
          const url = banner.match(/http:\/\/127\.0\.0\.1:\d+\//)?.[0];
          if (url && banner.includes("Ctrl-C stops the preview.")) resolve(url);
        });
      });
    });
    test.afterAll(async () => {
      if (server.exitCode !== null || server.signalCode !== null) return;
      const exited = once(server, "exit");
      server.kill("SIGTERM");
      await exited;
    });
    for (const theme of ["light", "dark"] as const) {
      test(`${theme} real console timeline`, async ({ page }) => {
        await page.clock.setFixedTime(new Date("2026-09-27T10:00:00.000Z"));
        await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
        const reading = page.waitForResponse(response => new URL(response.url()).pathname === `/api/macro-tasks/${macro}/timeline`);
        await open(page, `${baseURL}#tasks/${macro}`);
        const timeline = await (await reading).json();
        expect(timeline.truncated).toEqual({ rows: false, spans: false, events: false });
        expect(timeline.rows).toHaveLength(9);
        await expect(page.locator("[data-slot=timeline-axis]")).toBeVisible();
        await expect(page.locator("[data-slot=macro-entry]")).toHaveCount(4);
        await expect(page.locator(`[data-slot=macro-entry][aria-pressed=true]`)).toContainText(language === "zh" ? "交付离线搜索" : "Ship offline search");
        const failure = timeline.rows.find((row: { runId: string }) => row.runId === "readme-ranking");
        expect([failure.state, failure.status, failure.conclusionStatus]).toEqual(["concluded", "concluded", "failed"]);
        await expect(page.locator('[data-slot=timeline-row][data-run-id="readme-ranking"] [data-slot=failed-conclusion-mark] circle')).toBeVisible();
        await expect(page.locator('[data-slot=timeline-row-name][data-run-id="readme-ranking"]:visible')).toHaveAttribute("data-state-tone", "fail");
        await expect(page.locator("[data-slot=timeline-lane]")).toHaveCount(1);
        await expect(page.locator("[data-slot=timeline-row-name]:visible")).toHaveCount(9);
        await expect(page.locator('[data-slot=timeline-row-name][data-fixes="readme-ranking"]:visible')).toHaveCount(1);
        await expect(page.locator('[data-key="span:routing:att-readme-router"]')).toBeVisible();
        await expect(page.locator('[data-key="span:host:readme-guide-location"]')).toBeVisible();
        await expect(page.locator('[data-key="span:queue:readme-package"]')).toBeVisible();
        expect(await page.locator('[data-slot=timeline-break]').count()).toBeGreaterThan(0);
        expect(await page.locator('[data-slot=timeline-now]').count()).toBeGreaterThan(0);
        await expect(page.locator('[data-slot=event-cluster]').first()).toBeVisible();
        await expect(page.locator('[data-slot=inquiry-mark]')).toBeVisible();
        await expect(page.locator('[data-slot=silent-tail]')).toBeVisible();
        // A vertical SVG path has a zero-width box even when its stroke is drawn.
        await expect(page.locator('[data-slot=expected-mark]')).toHaveAttribute("d", /v32$/);
        await expect(page.locator('[data-slot=expected-mark]')).toHaveCSS("stroke-width", "2px");
        await expect(page.locator('[data-key="span:host:readme-guide-location"] [data-slot=host-wait]')).toHaveAttribute("stroke-dasharray", "5 4");
        const last = await page.locator('[data-slot=timeline-row][data-run-id="readme-package"]').boundingBox();
        expect(last!.y + last!.height).toBeLessThan(page.viewportSize()!.height - 35);
        await expectNoSidewaysScroll(page);
        await page.evaluate(() => document.fonts.ready);
        await settle(page);
        // Move the pointer off the content as a user would; leave every element
        // and every product animation intact. Capture only after paints settle.
        await page.mouse.move(0, 0);
        await page.screenshot({ path: `${output}console-timeline-${language}-${theme}.png` });
      });
    }
  });
}
