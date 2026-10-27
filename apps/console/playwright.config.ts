import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const repository = fileURLToPath(new URL("../..", import.meta.url));
const assets = fileURLToPath(new URL("../../src/hey_my_buddy/console/assets", import.meta.url));
// Parallel checkouts can run their synthetic previews independently.
const port = Number(process.env.HMB_PREVIEW_PORT ?? 49737);
const previewPython = process.env.HMB_PREVIEW_PYTHON;

// Geometry is checked on the built console in a real browser (ADR-029). The page
// is served by the repository's synthetic preview, never by a blackboard.
// https://playwright.dev/docs/test-configuration
export default defineConfig({
  testDir: "./browser",
  outputDir: process.env.CI ? resolve(process.env.RUNNER_TEMP!, "logs", "playwright") : "./test-results/browser",
  testMatch: "*.check.ts",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${port}/`, browserName: "chromium", trace: process.env.CI ? "retain-on-failure" : "off" },
  // https://playwright.dev/docs/test-webserver
  webServer: {
    command: `${previewPython ? `"${previewPython}"` : "uv run --no-project python"} tests/probes/macro_task_console_preview.py --assets "${assets}" --port ${port}`,
    cwd: repository,
    url: `http://127.0.0.1:${port}/`,
    reuseExistingServer: false,
  },
});
