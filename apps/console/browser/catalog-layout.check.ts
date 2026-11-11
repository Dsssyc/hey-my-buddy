import { expect, type Locator, type Page } from "@playwright/test";
import type { Buddy, Snapshot } from "../src/types";
import { expectNoSidewaysScroll, open, screenshots, settle, test } from "./preview";

const CODEX = "codex / openai / gpt-5-codex";
const OPUS = "claude / anthropic / claude-opus-5-5";
const frame = async (target: Locator) => (await target.boundingBox())!;

/** Five ordered efforts exercise overflow without changing the shared synthetic preview. */
async function fiveEfforts(page: Page) {
  await page.route("**/api/console", async route => {
    const response = await route.fetch();
    const snapshot: Snapshot = await response.json();
    const template = snapshot.buddies.find(buddy => buddy.model === "claude-opus-5-5")!;
    const efforts = ["low", "medium", "high", "xhigh", "max"];
    snapshot.buddies = snapshot.buddies.filter(buddy => buddy.model !== template.model).concat(efforts.map((effort, effortRank): Buddy => ({
      ...template, effort, effortRank, buddyId: `claude:anthropic:claude-opus-5-5:${effort}`, enabled: effort === "high",
    })));
    await route.fulfill({ response, json: snapshot });
  });
}

async function selectedIsInside(drawer: Locator) {
  const viewport = await frame(drawer.locator("[data-slot=buddy-list-viewport]"));
  const row = await frame(drawer.locator("[data-slot=buddy-stop][aria-pressed=true]"));
  expect(row.y).toBeGreaterThanOrEqual(viewport.y - 0.5);
  expect(row.y + row.height).toBeLessThanOrEqual(viewport.y + viewport.height + 0.5);
}

/** A stable column leaves the settings in place as buddy names change. */
async function fixedWidthList(drawer: Locator) {
  expect((await frame(drawer.locator("[data-slot=buddy-list]"))).width).toBe(352);
}

for (const theme of ["dark", "light"] as const) {
  for (const width of [1440, 1920, 1100, 1099, 900, 899, 390]) test.describe(`catalog layout ${width}px ${theme}`, () => {
    test.use({ viewport: { width, height: 1000 }, locale: "zh-CN", reducedMotion: "reduce" });
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    });

    test("places the service above the buddy list and settings, and keeps the left column fixed", async ({ page }) => {
      await open(page, "#buddies/catalog");
      await page.getByRole("button", { name: CODEX, exact: true }).click();
      const drawer = page.getByRole("region", { name: CODEX, exact: true });
      await settle(page);
      const service = await frame(drawer.locator("[data-slot=model-service]"));
      const list = await frame(drawer.locator("[data-slot=buddy-list]"));
      const panel = await frame(drawer.locator("[data-slot=buddy-panel]"));
      expect(service.y + service.height).toBeLessThanOrEqual(list.y);
      if (width >= 900) {
        expect(Math.abs(list.y - panel.y)).toBeLessThanOrEqual(0.5);
        expect(Math.abs(list.height - panel.height)).toBeLessThanOrEqual(0.5);
        expect(Math.abs(list.x + list.width - panel.x)).toBeLessThanOrEqual(0.5);
        await fixedWidthList(drawer);
        const heading = await frame(drawer.locator("[data-slot=buddy-list] > div").first());
        const samples = await frame(drawer.locator("[data-slot=buddy-samples]").locator(".."));
        expect(Math.abs(heading.y - samples.y)).toBeLessThanOrEqual(1);
        expect(Math.abs(heading.height - samples.height)).toBeLessThanOrEqual(1);
        const clearance = await drawer.locator("[data-slot=buddy-list]").evaluate(list => {
          const rows = [...list.querySelectorAll('[data-slot="buddy-stop"]')];
          const widest = rows.reduce((a, b) => a.lastElementChild!.getBoundingClientRect().right > b.lastElementChild!.getBoundingClientRect().right ? a : b);
          const scrollbar = list.querySelector('[data-slot="scroll-area-scrollbar"]')!;
          return scrollbar.getBoundingClientRect().left - widest.lastElementChild!.getBoundingClientRect().right;
        });
        expect(clearance, "the widest effort's preference clears the scrollbar").toBeGreaterThanOrEqual(28);
        const controls = await drawer.locator("[data-slot=model-service-line] > *").evaluateAll(nodes => nodes.map(node => {
          const box = node.getBoundingClientRect(); return { top: box.top, bottom: box.bottom };
        }));
        expect(Math.max(...controls.map(box => box.top))).toBeLessThan(Math.min(...controls.map(box => box.bottom)));
      } else {
        expect(list.y + list.height).toBeLessThanOrEqual(panel.y);
        const viewport = drawer.locator("[data-slot=buddy-list-viewport]");
        expect(await viewport.evaluate(node => node.scrollHeight - node.clientHeight)).toBeLessThanOrEqual(1);
        expect(await viewport.evaluate(node => getComputedStyle(node).overflowY)).toBe("visible");
      }
      await expect(drawer).not.toContainText(`${CODEX} /`);
      await expect(drawer.locator("[data-slot=buddy-panel]").getByRole("switch")).toHaveCount(0);
      await expect(drawer.getByText("基础设施失败", { exact: true })).toHaveCount(0);
      await expect(drawer.locator("[data-slot=buddy-samples]")).toContainText("次");
      const note = await frame(drawer.getByRole("textbox", { name: "high 的偏好理由" }));
      expect(note.width).toBeLessThanOrEqual(448);
      await expectNoSidewaysScroll(page);
      if (width === 1440) await page.screenshot({ path: `${screenshots}/catalog-drawer-1440-${theme}.png` });
    });

    test("keeps harness facts beside the list or below it while the whole header folds models", async ({ page }) => {
      await open(page, "#buddies/catalog");
      const models = page.locator("[data-slot=catalog-models]");
      const sidebar = page.locator("[data-slot=harness-sidebar]");
      const [list, side] = [await frame(models), await frame(sidebar)];
      if (width >= 1100) {
        expect(side.x).toBeGreaterThanOrEqual(list.x + list.width - 0.5);
        expect(Math.abs(side.y - list.y)).toBeLessThanOrEqual(0.5);
        expect(side.width).toBe(384);
        const firstCard = sidebar.locator("[data-slot=harness-card]").first();
        const firstSection = models.locator("[data-slot=harness-section]").first();
        expect(Math.abs((await frame(firstCard)).y - (await frame(firstSection)).y)).toBeLessThanOrEqual(1);
        const title = await frame(firstCard.locator("h3"));
        const header = await frame(firstSection.locator("[data-slot=harness-header]"));
        expect(title.height).toBe(48);
        expect(Math.abs(title.height - header.height)).toBeLessThanOrEqual(1);
        if (width === 1440) await page.screenshot({ path: `${screenshots}/catalog-rest-1440-${theme}.png` });
        const before = await models.locator(":scope > [data-slot=scroll-area-viewport]").evaluate(node => node.scrollTop);
        await sidebar.locator(":scope > [data-slot=scroll-area-viewport]").evaluate(node => { node.scrollTop = node.scrollHeight; });
        expect(await models.locator(":scope > [data-slot=scroll-area-viewport]").evaluate(node => node.scrollTop)).toBe(before);
      } else expect(side.y).toBeGreaterThanOrEqual(list.y + list.height - 0.5);
      await expect(sidebar.getByRole("heading", { name: "Harness", exact: true })).toHaveCount(1);
      expect(await sidebar.getByRole("heading", { name: "Harness", exact: true }).evaluate(node => node.getBoundingClientRect().width)).toBe(1);
      for (const card of await page.locator("[data-slot=harness-card], [data-slot=harness-section]").all()) {
        const radius = await card.evaluate(node => ({ actual: getComputedStyle(node).borderRadius, token: getComputedStyle(node).getPropertyValue("--radius-card").trim() }));
        expect(radius.actual).toBe(radius.token);
      }
      for (const section of await models.locator("[data-slot=harness-section]").all()) await expect(section).toHaveCSS("overflow", "clip");
      expect(await sidebar.locator("[data-slot=harness-card] h3").allTextContents()).toEqual(await page.locator("[data-slot=harness-section]").evaluateAll(nodes => nodes.map(node => node.getAttribute("aria-label"))));
      const header = page.locator('[data-slot=harness-section][aria-label="Codex"] [data-slot=harness-header]');
      const folded = await header.getByRole("button").getAttribute("aria-expanded");
      await header.click({ position: { x: 1, y: 1 } });
      await expect(header.getByRole("button")).toHaveAttribute("aria-expanded", folded === "true" ? "false" : "true");
      await expect(page.locator("[data-slot=account-summary], [data-slot=cooling-details]")).toHaveCount(0);
      await expect(page.locator("[data-slot=harness-section] [data-slot=harness-details]")).toHaveCount(0);
      for (const card of await sidebar.locator("[data-slot=harness-card]").all()) {
        await expect(card.locator("[data-slot=harness-details] > [role=group]")).toHaveCount(2);
        await expect(card.getByRole("button", { name: "检测", exact: true })).toHaveCount(1);
        await expect(card.getByRole("button", { name: "指定路径", exact: true })).toHaveCount(1);
      }
      await expectNoSidewaysScroll(page);
      if (width === 390 && theme === "dark") {
        await models.scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${screenshots}/catalog-390-dark.png` });
      }
    });

    test("deep-links to a harness title in the sidebar without opening header details", async ({ page }) => {
      await open(page, "#buddies/catalog/harness/zcode");
      const title = page.locator("#harness-title-zcode");
      await expect(title).toBeFocused();
      await expect(title).toBeInViewport();
      if (width >= 1100) expect(await page.locator("[data-slot=harness-sidebar] > [data-slot=scroll-area-viewport]").evaluate(node => node.scrollTop)).toBeGreaterThan(0);
      else {
        expect(await page.locator("[data-slot=harness-sidebar]").evaluate(node => (node as HTMLElement).offsetTop)).toBeGreaterThan(0);
      }
      await expect(page.locator("[data-slot=account-summary]")).toHaveCount(0);
    });
  });

  test.describe(`five effort drawer ${theme}`, () => {
    test.use({ viewport: { width: 1440, height: 1000 }, locale: "zh-CN", reducedMotion: "reduce" });
    test("wraps POSIX and Windows paths between complete segments, and splits only oversized segments", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      const posix = "~/.nvm/versions/node/v24.21.0/bin/console-harness-with-hyphens";
      const windows = "C:\\Program Files\\Harness Tools\\verified-version\\console-harness.exe";
      const oversized = `~/harnesses/${"long-name".repeat(18)}/bin/runner`;
      await page.route("**/api/console", async route => {
        const response = await route.fetch();
        const snapshot: Snapshot = await response.json();
        snapshot.harnesses = snapshot.harnesses!.map(row => row.harness === "codex" ? { ...row, executable: posix }
          : row.harness === "zcode" ? { ...row, status: "missing", executable: windows,
            candidates: [{ path: windows, status: "missing" }, { path: oversized, status: "missing" }] } : row);
        await route.fulfill({ response, json: snapshot });
      });
      await open(page, "#buddies/catalog");
      const paths = page.locator("[data-slot=harness-path]");
      for (const path of [posix, windows]) {
        const matches = paths.filter({ hasText: path });
        expect(await matches.count()).toBe(path === posix ? 1 : 2);
        for (const target of await matches.all()) {
          const lines = await target.evaluate(node => {
            const segments = [...node.children];
            return segments.map(segment => {
              const range = document.createRange();
              range.selectNodeContents(segment.firstChild!);
              return [...range.getClientRects()].map(rect => rect.y);
            });
          });
          expect(new Set(lines.flat()).size, "the path must actually wrap").toBeGreaterThan(1);
          for (const segment of lines) expect(new Set(segment).size, "each fitting segment stays whole").toBe(1);
        }
      }
      const longPath = paths.filter({ hasText: oversized });
      const longSegment = longPath.locator(":scope > span").filter({ hasText: "long-name" });
      expect(await longSegment.evaluate(node => {
        const range = document.createRange(); range.selectNodeContents(node.firstChild!);
        return new Set([...range.getClientRects()].map(rect => rect.y)).size;
      })).toBeGreaterThan(1);
      expect(await longPath.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
      await expectNoSidewaysScroll(page);
      await page.screenshot({ path: `${screenshots}/catalog-paths-${theme}.png` });
    });

    test("keeps a buddy-only unavailable hint after the evaluation count", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await page.route("**/api/console", async route => {
        const response = await route.fetch();
        const snapshot: Snapshot = await response.json();
        snapshot.buddies = snapshot.buddies.map(buddy => buddy.model === "gpt-5-codex" && buddy.effort === "medium"
          ? { ...buddy, available: false, unavailableReason: { code: "catalog.native-unavailable", params: {} } } : buddy);
        await route.fulfill({ response, json: snapshot });
      });
      await open(page, "#buddies/catalog");
      await page.getByRole("button", { name: CODEX, exact: true }).click();
      const drawer = page.getByRole("region", { name: CODEX, exact: true });
      await drawer.getByRole("button", { name: "medium", exact: true }).click();
      const samples = drawer.locator("[data-slot=buddy-samples]");
      const unavailable = drawer.locator("[data-slot=buddy-panel]").getByText("不可用", { exact: true });
      await expect(unavailable).toHaveAttribute("aria-description", /.+/);
      expect((await frame(unavailable)).x).toBeGreaterThanOrEqual((await frame(samples)).x + (await frame(samples)).width);
      expect(Math.abs((await frame(unavailable)).y + (await frame(unavailable)).height / 2 - ((await frame(samples)).y + (await frame(samples)).height / 2))).toBeLessThanOrEqual(1);
    });

    test("scrolls just the left list to the selected buddy on opening and on selection", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await fiveEfforts(page);
      await open(page, "#buddies/catalog/buddy/claude%3Aanthropic%3Aclaude-opus-5-5%3Amax");
      const drawer = page.getByRole("region", { name: OPUS, exact: true });
      await expect(drawer.locator("[data-slot=buddy-panel]")).toBeFocused();
      await expect(drawer.locator("[data-slot=buddy-stop]")).toHaveCount(5);
      const viewport = drawer.locator("[data-slot=buddy-list-viewport]");
      expect(await viewport.evaluate(node => node.scrollHeight - node.clientHeight)).toBeGreaterThan(0);
      expect(await viewport.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
      await expect(drawer.locator("[data-slot=scroll-area-scrollbar]")).toBeVisible();
      expect(await drawer.locator("[data-slot=scroll-area-scrollbar]").evaluate(node => getComputedStyle(node).opacity)).toBe("1");
      await selectedIsInside(drawer);
      await fixedWidthList(drawer);
      if (theme === "dark") await page.screenshot({ path: `${screenshots}/catalog-opus-five-1440-dark.png` });
      // Keyboard selection remains local: focus the first row without scrolling ancestors,
      // then activate it through the same native button used by a pointer.
      const pageScroll = await page.locator("[data-slot=catalog-models] > [data-slot=scroll-area-viewport]").evaluate(node => node.scrollTop);
      await drawer.getByRole("button", { name: "low", exact: true }).evaluate(node => (node as HTMLElement).focus({ preventScroll: true }));
      await page.keyboard.press("Enter");
      await expect(drawer.getByRole("button", { name: "low", exact: true })).toHaveAttribute("aria-pressed", "true");
      await selectedIsInside(drawer);
      expect(await page.locator("[data-slot=catalog-models] > [data-slot=scroll-area-viewport]").evaluate(node => node.scrollTop)).toBe(pageScroll);
    });

    test("keeps the pending timestamp with service controls and puts range errors and notices underneath", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, "#buddies/catalog");
      await page.getByRole("button", { name: "zcode / zai / glm-5", exact: true }).click();
      const drawer = page.getByRole("region", { name: "zcode / zai / glm-5", exact: true });
      const line = drawer.locator("[data-slot=model-service-line]");
      await expect(line.locator("time")).toHaveCount(1);
      const [timestamp, meter] = [await frame(line.locator("time")), await frame(line.getByRole("meter"))];
      expect(timestamp.y).toBeLessThan(meter.y + meter.height);
      expect(timestamp.y + timestamp.height).toBeGreaterThan(meter.y);
      const limit = drawer.getByRole("spinbutton");
      await limit.fill("0");
      const error = drawer.locator("[data-slot=model-service]").getByRole("alert");
      expect((await frame(error)).y).toBeGreaterThanOrEqual((await frame(line)).y + (await frame(line)).height);
      await limit.fill("3");
      await expect(error).toHaveCount(0);
      const note = drawer.getByRole("note");
      expect((await frame(note)).y).toBeGreaterThanOrEqual((await frame(line)).y + (await frame(line)).height);
      expect((await frame(note)).y + (await frame(note)).height).toBeLessThanOrEqual((await frame(drawer.locator("[data-slot=buddy-list]"))).y);
    });

    test("uses natural list height for five efforts on a narrow screen", async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 1000 });
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await fiveEfforts(page);
      await open(page, "#buddies/catalog/buddy/claude%3Aanthropic%3Aclaude-opus-5-5%3Amax");
      const drawer = page.getByRole("region", { name: OPUS, exact: true });
      const viewport = drawer.locator("[data-slot=buddy-list-viewport]");
      await expect(drawer.locator("[data-slot=buddy-stop]")).toHaveCount(5);
      expect(await viewport.evaluate(node => node.scrollHeight - node.clientHeight)).toBeLessThanOrEqual(1);
      const [list, panel] = [await frame(drawer.locator("[data-slot=buddy-list]")), await frame(drawer.locator("[data-slot=buddy-panel]"))];
      expect(list.y + list.height).toBeLessThanOrEqual(panel.y);
      await selectedIsInside(drawer);
      await expectNoSidewaysScroll(page);
    });

    test("toggles in the left list independently of selection and paints selection to the divider", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, "#buddies/catalog");
      await page.getByRole("button", { name: CODEX, exact: true }).click();
      const drawer = page.getByRole("region", { name: CODEX, exact: true });
      const selected = drawer.locator("[data-slot=buddy-stop][aria-pressed=true]");
      const selectedName = await selected.getAttribute("aria-label");
      const medium = drawer.getByRole("switch", { name: "启用 medium", exact: true });
      await medium.click();
      await expect(medium).toHaveAttribute("aria-checked", "true");
      await expect(selected).toHaveAttribute("aria-label", selectedName!);
      await drawer.getByRole("button", { name: "medium", exact: true }).click();
      await expect(drawer.getByRole("button", { name: "medium", exact: true })).toHaveAttribute("aria-pressed", "true");
      const choice = drawer.locator('[data-slot=buddy-choice][data-selected="true"]');
      const row = await frame(choice), list = await frame(drawer.locator("[data-slot=buddy-list]")), panel = await frame(drawer.locator("[data-slot=buddy-panel]"));
      expect(row.x).toBe(list.x);
      expect(row.x + row.width).toBe(panel.x);
      const colour = await choice.evaluate(node => getComputedStyle(node).backgroundColor);
      expect(colour).not.toBe("rgba(0, 0, 0, 0)");
      await choice.hover();
      expect(await choice.evaluate(node => getComputedStyle(node).backgroundColor)).toBe(colour);
      await expect(drawer.locator("[data-slot=buddy-list]").getByRole("switch")).toHaveCount(3);
    });
  });
}
