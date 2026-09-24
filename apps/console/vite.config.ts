import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // Private console URLs have a runtime prefix: https://vite.dev/guide/build.html#relative-base
  base: "./",
  build: { outDir: "../../src/buddy/console_assets", emptyOutDir: true },
  test: { environment: "jsdom", clearMocks: true },
});
