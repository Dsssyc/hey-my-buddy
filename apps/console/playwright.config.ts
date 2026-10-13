import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

const repository = fileURLToPath(new URL("../..", import.meta.url));
const assets = fileURLToPath(new URL("../../src/hey_my_buddy/console/assets", import.meta.url));
const port = 49737;

// Geometry is checked on the built console in a real browser (ADR-029). The page
// is served by the repository's synthetic preview, never by a blackboard.
// https://playwright.dev/docs/test-configuration
export default defineConfig({
  testDir: "./browser",
  testMatch: "*.check.ts",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${port}/`, browserName: "chromium" },
  // https://playwright.dev/docs/test-webserver
  webServer: {
    command: `uv run --no-project python tests/probes/macro_task_console_preview.py --assets "${assets}" --port ${port}`,
    cwd: repository,
    url: `http://127.0.0.1:${port}/`,
    reuseExistingServer: false,
  },
});
