#!/usr/bin/env node
/** Capture the checked-in design sketches at the same viewports as the live list shots. */
import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const repository = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(join(repository, "apps/console/package.json"));
const { chromium } = require("playwright");
const out = resolve(process.argv[2] || join(repository, "tmp/list-shots"));
const source = join(repository, "docs/design/console-frontend");
const dictionary = new Map(Object.entries({
  "任务": "Tasks", "设置": "Settings", "宏任务": "Macro tasks", "全部": "All", "进行中": "Running",
  "等待 Host": "Waiting for Host", "等待验收": "Awaiting acceptance", "已验收": "Accepted", "项目": "Project",
  "开始": "Started", "历时": "Elapsed", "时间轴": "Timeline", "连接中断": "Disconnected",
  "重试连接": "Reconnect", "搜索标题、项目、Host 或编号": "Search title, project, Host or ID",
  "还没有宏任务": "No macro tasks yet", "选择一个宏任务": "Choose a macro task",
}));

await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  for (const locale of ["zh-CN", "en-US"]) for (const theme of ["light", "dark"]) for (const width of [1280, 390]) {
    const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 800 }, locale });
    const files = width === 390
      ? [["list", "tasks-narrow.html", 0], ["detail", "tasks-narrow.html", 1], ["empty", "tasks-empty.html", null], ["offline", "tasks-offline.html", null]]
      : [["list", "tasks.html", null], ["detail", "tasks.html", null], ["empty", "tasks-empty.html", null], ["offline", "tasks-offline.html", null]];
    for (const [state, name, panel] of files) {
      await page.goto(pathToFileURL(join(source, name)).href);
      await page.evaluate(({ theme, english, entries }) => {
        document.documentElement.dataset.theme = theme;
        if (!english) return;
        const dictionary = new Map(entries);
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) {
          const node = walker.currentNode;
          const current = node.textContent ?? "";
          const translated = dictionary.get(current.trim());
          if (translated) node.textContent = current.replace(current.trim(), translated);
        }
      }, { theme, english: locale === "en-US", entries: [...dictionary] });
      const path = join(out, `mock-${state}-${locale}-${width}-${theme}.png`);
      if (panel === null) await page.screenshot({ path });
      else await page.locator(".ph2").nth(panel).screenshot({ path });
    }
    await page.close();
  }
} finally {
  await browser.close();
}
console.log(`Static sketch screenshots: ${out}. English sketches translate primary labels only; the underlying design text remains Chinese.`);
