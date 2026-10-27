import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  // Private console URLs have a runtime prefix: https://vite.dev/guide/build.html#relative-base
  base: "./",
  // README and console share the canonical logo; keep the extra read scope narrow.
  server: { fs: { allow: [fileURLToPath(new URL(".", import.meta.url)), fileURLToPath(new URL("../../docs/assets", import.meta.url))] } },
  build: { outDir: "../../src/hey_my_buddy/console/assets", emptyOutDir: true },
  // The palette reads the real token source; Vitest must not stub its CSS import.
  test: { environment: "jsdom", css: { include: /tokens\.css/ }, clearMocks: true, setupFiles: ["./src/test-setup.ts"] },
});
