import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expectNoSidewaysScroll, open, test } from "./preview";
import { expect } from "@playwright/test";

const captures = fileURLToPath(new URL("../../../tmp/perf-p23/", import.meta.url));

for (const width of [1440, 390]) for (const theme of ["dark", "light"]) {
  test.describe(`list read actions ${width}px ${theme}`, () => {
    test.use({ viewport: { width, height: 900 }, locale: "zh-CN", reducedMotion: "reduce" });
    test("pages while a background read is pending and shows the action as busy", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      let reads = 0, paging = false;
      let finishPoll!: () => void, finishPage!: () => void;
      const pollGate = new Promise<void>(resolve => { finishPoll = resolve; });
      const pageGate = new Promise<void>(resolve => { finishPage = resolve; });
      await page.route(/\/api\/macro-tasks(?:\?|$)/, async route => {
        const url = new URL(route.request().url());
        const source = new URL(url);
        source.searchParams.delete("before");
        const response = await route.fetch({ url: source.toString() });
        const data = await response.json();
        data.macroTasks = data.macroTasks.filter((row: { kind: string }) => row.kind !== "standalone").slice(0, 3);
        data.nextCursor = "read-actions-next";
        reads++;
        if (url.searchParams.has("before")) {
          paging = true;
          await pageGate;
          data.macroTasks = [{ ...data.macroTasks[0], macroTaskId: "read-actions-older", title: "更早的合成宏任务" }];
          data.nextCursor = null;
        } else if (reads > 1) await pollGate;
        await route.fulfill({ json: data });
      });
      try {
        await open(page, "#tasks");
        const more = page.getByRole("button", { name: "更早", exact: true });
        await expect(more).toBeVisible();
        await mkdir(captures, { recursive: true });
        await page.screenshot({ path: `${captures}/list-${width}-${theme}.png`, animations: "disabled" });
        // The test observes the synthetic service's poll before requesting a page.
        await expect.poll(() => reads).toBe(2);
        await more.click();
        await expect.poll(() => paging).toBe(true);
        const busy = page.locator('[data-slot=macro-list] button[aria-busy="true"]:disabled');
        await expect(busy).toHaveText("读取中");
        await expect(busy).toBeDisabled();
        await expectNoSidewaysScroll(page);
        await mkdir(captures, { recursive: true });
        await page.screenshot({ path: `${captures}/list-${width}-${theme}-paging.png`, animations: "disabled" });
        finishPoll();
        // An aborted poll must not clear the busy state of the user's read.
        await expect(busy).toBeDisabled();
        finishPage();
        await expect(page.getByRole("button", { name: /更早的合成宏任务/ })).toBeVisible();
        await expect(page.locator('[data-slot=macro-list] button[aria-busy="true"]')).toHaveCount(0);
      } finally { finishPoll(); finishPage(); }
    });
  });
}
