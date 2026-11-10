import { expect, type Locator, type Page, type TestInfo } from "@playwright/test";
import { expectNoSidewaysScroll, open, test } from "./preview";

const instant = (index: number) => new Date(Date.UTC(2026, 10, 10, 0, index)).toISOString();

async function capture(page: Page, work: string, theme: string, testInfo: TestInfo) {
  await page.screenshot({ path: testInfo.outputPath(`${work}-${theme}.png`) });
}

async function syntheticRows(page: Page, count: number, extraBuddy = false) {
  await page.route("**/api/console", async route => {
    const response = await route.fetch();
    const snapshot = await response.json();
    const buddy = snapshot.buddies.find((row: { buddyId: string }) => row.buddyId === snapshot.configuration.routerBuddyIds[0]);
    const router = snapshot.routingHealth.routers[0];
    const failure = snapshot.routingHealth.recentFailures[0];
    const maintenance = snapshot.maintenance.runs[0];
    const model = snapshot.models.find((row: { latestProfileRun: unknown }) => row.latestProfileRun);
    const ids = Array.from({ length: count + Number(extraBuddy) }, (_, index) => `synthetic-router-${index}`);
    snapshot.buddies = [...snapshot.buddies, ...ids.map((buddyId, index) => ({
      ...buddy, buddyId, model: `Long synthetic Router model ${index} with four identity parts`,
    }))];
    snapshot.configuration.routerBuddyIds = ids.slice(0, count);
    snapshot.routingHealth.routers = ids.slice(0, count).map((buddyId, index) => ({
      ...router, buddyId, index: index + 1, identity: { ...router.identity, model: `Long synthetic Router model ${index}` },
    }));
    snapshot.routingHealth.recentFailures = Array.from({ length: count }, (_, index) => ({
      ...failure, decisionId: `synthetic-failure-${index}`, at: instant(index), title: `Synthetic failure ${index}`,
    }));
    snapshot.maintenance.runs = Array.from({ length: count }, (_, index) => ({
      ...maintenance, maintenanceRunId: `synthetic-maintenance-${index}`, state: "completed", reason: null,
      startedAt: instant(index), finishedAt: instant(index + 1),
    }));
    snapshot.models = Array.from({ length: count }, (_, index) => ({
      ...model, modelId: `synthetic-profile-${index}`, displayName: `Synthetic profile ${index}`,
      latestProfileRun: { ...model.latestProfileRun, profileRunId: `synthetic-profile-run-${index}`,
        startedAt: instant(index), finishedAt: instant(index + 1), state: "completed", reason: null, correctionRounds: index },
    }));
    await route.fulfill({ response, json: snapshot });
  });
}

async function dimensions(region: Locator, count: number, headerRows = 1) {
  await expect(region).toHaveAttribute("data-slot", /.+/);
  const viewport = region.locator(':scope > [data-slot="scroll-area-viewport"]');
  const table = viewport.locator("table").first();
  await expect(table.locator(":scope > tbody > tr")).toHaveCount(count);
  if (headerRows === 0) {
    await expect(table.locator(":scope > thead")).toHaveClass(/sr-only/);
    await expect(table.locator(":scope > thead th")).toHaveText(["时间", "结果", "微任务"]);
  }
  const measure = await viewport.evaluate(node => ({ height: node.getBoundingClientRect().height,
    scrollHeight: node.scrollHeight, clientHeight: node.clientHeight }));
  expect(measure.height).toBeCloseTo((Math.min(count, 6) + headerRows) * 44, 0);
  if (count <= 6) expect(measure.scrollHeight - measure.clientHeight).toBeLessThanOrEqual(1);
  else {
    const scrollbar = region.locator(':scope > [data-slot="scroll-area-scrollbar"][data-orientation="vertical"]');
    await expect(scrollbar).toBeVisible();
    await expect(scrollbar).toHaveCSS("opacity", "1");
    expect(measure.scrollHeight - measure.clientHeight).toBeGreaterThan(44);
    const header = table.locator("th").first();
    const before = headerRows ? (await header.boundingBox())!.y : null;
    await viewport.evaluate(node => { node.scrollTop = 44; });
    await expect.poll(() => viewport.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
    if (before !== null) expect((await header.boundingBox())!.y).toBeCloseTo(before, 0);
  }
  const rowSelector = headerRows ? ":scope > thead > tr, :scope > tbody > tr" : ":scope > tbody > tr";
  const rows = await table.locator(rowSelector).evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height));
  expect(rows.every(height => Math.abs(height - 44) <= 1), `44px rows: ${rows}`).toBe(true);
  const rules = await table.locator("th, td").evaluateAll(nodes => nodes.map(node => {
    const style = getComputedStyle(node);
    return [style.borderTopWidth, style.borderBottomWidth];
  }));
  expect(rules.every(widths => widths.every(width => width === "0px"))).toBe(true);
}

for (const theme of ["dark", "light"] as const) test.describe(`Router cards ${theme}`, () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  test.beforeEach(async ({ page }) => page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme));

  test("uses full-width cards and aligned settings and readings", async ({ page }, testInfo) => {
    await open(page, "#buddies/router/routing");
    await capture(page, "routing", theme, testInfo);
    const detail = page.locator('[data-slot="router-detail"]');
    const cards = detail.locator('[data-slot="router-card"]');
    await expect(cards).toHaveCount(3);
    const geometry = await cards.evaluateAll(nodes => nodes.map(node => {
      const box = node.getBoundingClientRect();
      const header = node.querySelector('[data-slot="router-card-header"]')!;
      const body = header.nextElementSibling!;
      const border = getComputedStyle(node);
      return { x: box.x, width: box.width, top: box.top, bottom: box.bottom,
        radius: border.borderRadius, radiusToken: border.getPropertyValue("--radius-card").trim(),
        headerBorder: getComputedStyle(header).borderBottomWidth, headerHeight: header.getBoundingClientRect().height,
        bodyLeft: parseFloat(getComputedStyle(body).paddingLeft), bodyRight: parseFloat(getComputedStyle(body).paddingRight) };
    }));
    for (const card of geometry) {
      expect(card.x).toBe(geometry[0].x);
      expect(card.width).toBe(geometry[0].width);
      expect(card.radius).toBe("6px");
      expect(card.radius).toBe(card.radiusToken);
      expect(card.headerBorder).toBe("0px");
      expect(card.headerHeight).toBe(48);
      expect(card.bodyLeft).toBe(16);
      expect(card.bodyRight).toBe(16);
    }
    for (let index = 1; index < geometry.length; index++) expect(geometry[index].top - geometry[index - 1].bottom).toBe(16);
    const settings = detail.locator(".router-settings");
    const readings = detail.locator(".routing-readings");
    await expect(settings).toHaveCSS("grid-template-columns", /.+ .+/);
    await expect(settings).toHaveCSS("column-gap", "32px");
    await expect(readings).toHaveCSS("column-gap", "32px");
    const readingCells = await readings.locator(":scope > *").evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().x));
    const failureCells = await detail.locator('[data-slot="routing-failures-region"] tbody > tr').first().locator("td").evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().x));
    expect(failureCells).toHaveLength(3);
    for (let index = 0; index < 3; index++) expect(failureCells[index]).toBeCloseTo(readingCells[index], 0);
    const failureCenters = await detail.locator('[data-slot="routing-failures-region"] tbody > tr').first().locator("td").evaluateAll(nodes => nodes.map(node => {
      const child = node.firstElementChild!.getBoundingClientRect();
      return child.y + child.height / 2;
    }));
    for (const center of failureCenters) expect(Math.abs(center - failureCenters[0])).toBeLessThanOrEqual(1);
    const largeNumber = readings.locator(":scope > *").first().locator(".text-lg");
    await expect(largeNumber).toHaveCount(1);
    await expect(largeNumber).toHaveCSS("font-size", "18px");
    await expect(largeNumber).toHaveCSS("font-weight", "600");
    await expectNoSidewaysScroll(page);

    for (const [hash, expected] of [["maintenance", 3], ["profiles", 2]] as const) {
      await page.goto(`#buddies/router/${hash}`);
      await expect(page.locator('[data-slot="router-card"]')).toHaveCount(expected);
      if (hash === "maintenance") {
        const fields = await page.locator('[data-slot="maintenance-plane"] .router-settings [data-slot="router-field"]').evaluateAll(nodes => nodes.map(node => {
          const box = node.getBoundingClientRect();
          return { x: box.x, y: box.y, width: box.width, height: box.height };
        }));
        expect(fields).toHaveLength(2);
        expect(fields[0].y).toBeCloseTo(fields[1].y, 0);
        expect(fields[0].height).toBeCloseTo(fields[1].height, 0);
        expect(fields[1].x).toBeGreaterThan(fields[0].x + fields[0].width);
      }
      await capture(page, hash, theme, testInfo);
    }
  });

  test("keeps count popovers transient despite old inline preferences", async ({ page }) => {
    const keys = [
      "quant-bar.routing.dsh:deepseek-official:deepseek-flash:max",
      "quant-bar.usage.maintenance-run.preview-maintenance-1",
      "quant-bar.usage.profile-run.synthetic-profile-run-failed",
    ].map(name => `hey-my-buddy.console.view.${name}`);
    await page.addInitScript(keys => {
      for (const key of keys) localStorage.setItem(key, "true");
    }, keys);
    await open(page, "#buddies/router/routing");
    for (const [work, region] of [
      ["routing", "routing-routers-region"], ["maintenance", "maintenance-records-region"], ["profiles", "profile-records"],
    ] as const) {
      await page.goto(`#buddies/router/${work}`);
      const trigger = page.locator(`[data-slot="${region}"]`).locator('[data-slot="quant-bar-trigger"]').first();
      await expect(trigger).toHaveAttribute("aria-expanded", "false");
      await expect(page.locator('[data-slot="quant-bar-detail"]')).toHaveCount(0);
      await trigger.click();
      await expect(page.locator('[data-slot="quant-bar-detail"]')).toBeVisible();
      await page.reload();
      await expect(trigger).toHaveAttribute("aria-expanded", "false");
      await expect(page.locator('[data-slot="quant-bar-detail"]')).toHaveCount(0);
      await trigger.click();
      await page.keyboard.press("Escape");
      expect(await page.evaluate(keys => keys.map(key => localStorage.getItem(key)), keys)).toEqual(keys.map(() => "true"));
    }
  });

  test("keeps record rows 44px when a disclosure opens", async ({ page }) => {
    await open(page, "#buddies/router/routing");
    for (const [work, selector] of [
      ["routing", '[data-slot="routing-routers-region"]'],
      ["maintenance", '[data-slot="maintenance-records-region"]'],
      ["profiles", '[data-slot="profile-records"]'],
    ] as const) {
      if (work !== "routing") await page.goto(`#buddies/router/${work}`);
      const row = page.locator(`${selector} tbody > tr`).first();
      const disclosure = row.locator('[data-slot="quant-bar-trigger"]').first();
      await disclosure.click();
      await expect(disclosure).toHaveAttribute("aria-expanded", "true");
      expect((await row.boundingBox())!.height, `${work} expanded row`).toBeCloseTo(44, 0);
    }
  });

  for (const count of [1, 3, 6, 8]) test(`sizes four record regions at ${count} rows`, async ({ page }) => {
    await syntheticRows(page, count);
    await open(page, "#buddies/router/routing");
    await dimensions(page.locator('[data-slot="routing-routers-region"]'), count);
    await dimensions(page.locator('[data-slot="routing-failures-region"]'), count, 0);
    await page.goto("#buddies/router/maintenance");
    await dimensions(page.locator('[data-slot="maintenance-records-region"]'), count);
    await page.goto("#buddies/router/profiles");
    await dimensions(page.locator('[data-slot="profile-records"]'), count);
  });

  test("scrolls to a newly added Router after the sixth row", async ({ page }) => {
    await syntheticRows(page, 6, true);
    await open(page, "#buddies/router/routing");
    const region = page.locator('[data-slot="routing-routers-region"]');
    const viewport = region.locator(':scope > [data-slot="scroll-area-viewport"]');
    await expect(region.locator("tbody > tr")).toHaveCount(6);
    await page.getByRole("group", { name: "Router 列表" }).getByRole("button", { name: "添加", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: /Long synthetic Router model 6/ }).click();
    await expect(region.locator("tbody > tr")).toHaveCount(7);
    await expect.poll(() => viewport.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
    const last = (await region.locator("tbody > tr").last().boundingBox())!;
    const frame = (await viewport.boundingBox())!;
    expect(last.y + last.height).toBeLessThanOrEqual(frame.y + frame.height + 1);
  });

  test("focuses and scrolls to the profile run named in the address", async ({ page }) => {
    await syntheticRows(page, 8);
    await open(page, "#buddies/router/profiles/synthetic-profile-run-0");
    const region = page.locator('[data-slot="profile-records"]');
    const viewport = region.locator(':scope > [data-slot="scroll-area-viewport"]');
    const target = region.getByRole("button", { name: "Synthetic profile 0", exact: true });
    await expect(target).toBeFocused();
    await expect(target).toHaveAttribute("aria-expanded", "true");
    await expect.poll(() => viewport.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
    const item = (await target.boundingBox())!;
    const frame = (await viewport.boundingBox())!;
    expect(item.y).toBeGreaterThanOrEqual(frame.y - 1);
    expect(item.y + item.height).toBeLessThanOrEqual(frame.y + frame.height + 1);
  });

  test("keeps only the profile record header sticky above an expanded run", async ({ page }) => {
    await syntheticRows(page, 8);
    await open(page, "#buddies/router/profiles/synthetic-profile-run-0");
    const region = page.locator('[data-slot="profile-records"]');
    const viewport = region.locator(':scope > [data-slot="scroll-area-viewport"]');
    const mainHeader = region.locator("table").first().locator(":scope > thead th").first();
    const nestedHeaders = region.locator("table table th");
    expect(await nestedHeaders.count()).toBeGreaterThan(0);
    await viewport.evaluate(node => { node.scrollTop = node.scrollHeight; });
    await expect.poll(() => viewport.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
    const frame = (await viewport.boundingBox())!;
    expect((await mainHeader.boundingBox())!.y).toBeCloseTo(frame.y, 0);
    expect(await nestedHeaders.evaluateAll(nodes => nodes.every(node => getComputedStyle(node).position !== "sticky"))).toBe(true);
  });

  test("keeps long Router names on one line at 1100px and scrolls records inside narrow cards", async ({ page }) => {
    await syntheticRows(page, 8);
    await page.setViewportSize({ width: 1100, height: 900 });
    await open(page, "#buddies/router/routing");
    const region = page.locator('[data-slot="routing-routers-region"]');
    const name = region.locator("tbody > tr").first().getByRole("link");
    const lineCount = await name.evaluate(node => {
      const range = document.createRange(); range.selectNodeContents(node);
      return new Set([...range.getClientRects()].filter(rect => rect.width > 0).map(rect => rect.y)).size;
    });
    expect(lineCount).toBe(1);
    await expectNoSidewaysScroll(page);
    await page.setViewportSize({ width: 390, height: 844 });
    const viewport = region.locator(':scope > [data-slot="scroll-area-viewport"]');
    expect(await viewport.evaluate(node => node.scrollWidth - node.clientWidth)).toBeGreaterThan(0);
    await viewport.evaluate(node => { node.scrollLeft = node.scrollWidth; });
    await expect.poll(() => viewport.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
    await expectNoSidewaysScroll(page);
  });
});

for (const theme of ["dark", "light"] as const) for (const width of [1280, 1360, 1440, 390]) test.describe(`Router validation ${theme} ${width}px`, () => {
  test.use({ locale: "en-US", viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test("wraps invalid input messages within their own setting without scrolling", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, "#buddies/router/routing");
    const invalid = [
      ["Response start timeout", "Response start timeout", "Enter an integer from 1 to 86400"],
      ["Retry interval", "Retry interval in seconds", "Enter an integer from 1 to 2147483647"],
      ["Exploration quota", "Micro tasks per exploration quota", "Enter an integer of at least 2"],
    ] as const;
    for (const [, input] of invalid) await page.getByRole("spinbutton", { name: input, exact: true }).fill("0");
    for (const [name, input, message] of invalid) {
      const field = page.locator('[data-slot="router-field"]').filter({ has: page.getByRole("spinbutton", { name: input, exact: true }) });
      await expect(field.getByText(message, { exact: true })).toBeVisible();
      await field.scrollIntoViewIfNeeded();
      const geometry = await field.evaluate((node, message) => {
        const bounds = node.getBoundingClientRect();
        const error = [...node.querySelectorAll("[aria-live]")].find(node => node.textContent === message)!;
        const range = document.createRange(); range.selectNodeContents(error);
        const rects = [...range.getClientRects()].filter(rect => rect.width > 0);
        return { height: bounds.height, overflow: node.scrollWidth - node.clientWidth,
          scrollAreas: node.querySelectorAll('[data-slot="scroll-area"]').length,
          visible: rects.every(rect => rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1 && rect.top >= bounds.top - 1 && rect.bottom <= bounds.bottom + 1) };
      }, message);
      expect(geometry.visible, `${name}: complete error text stays inside its field`).toBe(true);
      expect(geometry.overflow, name).toBeLessThanOrEqual(1);
      expect(geometry.scrollAreas, name).toBe(0);
      expect(geometry.height, name).toBeGreaterThanOrEqual(44);
    }
    await expectNoSidewaysScroll(page);
  });
});

for (const locale of ["zh-CN", "en-US"] as const) for (const theme of ["dark", "light"] as const) for (const width of [1280, 1440]) test.describe(`Router setting alignment ${locale} ${theme} ${width}px`, () => {
  test.use({ locale, viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test("shares label widths and aligns controls while unsaved marks are present", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, "#buddies/router/routing");
    const fields = page.locator('[data-slot="routing-work"] .router-settings > [data-slot="router-field"]');
    await expect(fields).toHaveCount(4);
    const assertAligned = async () => {
      const geometry = await fields.evaluateAll(nodes => nodes.map(node => ({
        labelWidth: node.firstElementChild!.getBoundingClientRect().width,
        controlLeft: node.querySelector('[data-slot="segmented"], input')!.getBoundingClientRect().left,
      })));
      for (const [first, second] of [[0, 2], [1, 3]]) {
        expect(Math.abs(geometry[first].labelWidth - geometry[second].labelWidth)).toBeLessThanOrEqual(1);
        expect(Math.abs(geometry[first].controlLeft - geometry[second].controlLeft)).toBeLessThanOrEqual(1);
      }
    };
    await assertAligned();
    await fields.nth(0).getByRole("radio", { checked: false }).first().click();
    await fields.nth(1).getByRole("spinbutton").fill("61");
    await expect(page.locator('[data-slot="router-field"] [data-slot="unsaved-mark"]')).toHaveCount(2);
    await assertAligned();
    await fields.nth(2).getByRole("spinbutton").fill("601");
    await fields.nth(3).getByRole("spinbutton").fill("7");
    await expect(page.locator('[data-slot="router-field"] [data-slot="unsaved-mark"]')).toHaveCount(4);
    await assertAligned();
  });
});

for (const locale of ["zh-CN", "en-US"] as const) for (const theme of ["dark", "light"] as const) for (const width of [390, 1440]) test.describe(`Router setting order ${locale} ${theme} ${width}px`, () => {
  test.use({ locale, viewport: { width, height: 1000 }, reducedMotion: "reduce" });
  test("preserves reading, visual and keyboard order across the shared grid", async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
    await open(page, "#buddies/router/routing");
    const fields = page.locator('[data-slot="routing-work"] .router-settings > [data-slot="router-field"]');
    const names = locale === "zh-CN" ? ["默认模式", "开始作答时限", "重试间隔", "探索名额"]
      : ["Default mode", "Response start timeout", "Retry interval", "Exploration quota"];
    expect(await fields.evaluateAll(nodes => nodes.map(node => document.getElementById(node.getAttribute("aria-labelledby")!)!.textContent))).toEqual(names);
    const positions = await fields.evaluateAll(nodes => nodes.map(node => ({ x: node.getBoundingClientRect().x, y: node.getBoundingClientRect().y })));
    if (width === 390) {
      for (let index = 1; index < positions.length; index++) {
        expect(positions[index].x).toBeCloseTo(positions[0].x, 0);
        expect(positions[index].y).toBeGreaterThan(positions[index - 1].y);
      }
    } else {
      for (const [left, right] of [[0, 1], [2, 3]]) {
        expect(positions[left].y).toBeCloseTo(positions[right].y, 0);
        expect(positions[left].x).toBeLessThan(positions[right].x);
      }
    }
    const helps = fields.locator('[data-slot="router-setting-help"]');
    await helps.nth(0).focus();
    await page.keyboard.press("Tab");
    await expect(fields.nth(0).getByRole("radio", { checked: true })).toBeFocused();
    for (const index of [1, 2, 3]) {
      await page.keyboard.press("Tab");
      await expect(helps.nth(index)).toBeFocused();
      if (index < 3) {
        await page.keyboard.press("Tab");
        await expect(fields.nth(index).getByRole("spinbutton")).toBeFocused();
      }
    }
  });
});
