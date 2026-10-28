import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

// An explicit capture command: never included in the ordinary browser suite.
export default defineConfig({
  testDir: ".",
  testMatch: "readme.shot.ts",
  outputDir: fileURLToPath(new URL("../../../tmp/readme-shot/playwright", import.meta.url)),
  workers: 1,
  fullyParallel: false,
  reporter: "list",
  use: {
    browserName: "chromium",
    viewport: { width: 1280, height: 820 },
    deviceScaleFactor: 2,
    timezoneId: "UTC",
    reducedMotion: "reduce",
    trace: "off",
  },
});
