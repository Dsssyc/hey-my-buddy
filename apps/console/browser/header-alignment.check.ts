import { expect, type Locator } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { open, settle, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const captures = fileURLToPath(new URL("../../../tmp/console-align", import.meta.url));
const goals = [
  { body: "目标说明\n\n保持正文舒适的阅读行宽。".repeat(8), revision: 2, createdAt: "2026-11-01T06:02:00Z" },
  { body: "最初的完整目标。", revision: 1, createdAt: "2026-10-31T19:41:00Z" },
];

// A zero-height inline box sits on the actual text baseline, independent of font metrics.
async function identifierGeometry(identifier: Locator) {
  return identifier.evaluate(button => {
    const baseline = (element: Element) => {
      const marker = document.createElement("span");
      marker.style.cssText = "display:inline-block;width:0;height:0;vertical-align:baseline";
      element.append(marker); const y = marker.getBoundingClientRect().top; marker.remove(); return y;
    };
    const value = button.querySelector("span")!;
    const valueBaseline = baseline(value);
    const pair = button.closest("dd")?.previousElementSibling
      ?? button.closest('[data-slot="section"]')?.querySelector(":scope > span");
    const facts = button.closest('[data-slot="macro-overview-facts"]');
    const neighbours = facts ? [...facts.querySelectorAll("dt,dd")].filter(dt => {
      const rect = dt.getBoundingClientRect(), own = value.getBoundingClientRect();
      return Math.abs(rect.top - own.top) < 12;
    }) : [];
    const svg = button.querySelector("svg")!.getBoundingClientRect(), rect = button.getBoundingClientRect();
    return { valueBaseline, labelBaseline: pair ? baseline(pair) : null,
      neighbours: neighbours.map(baseline), height: rect.height,
      factHeight: facts ? button.closest("dd")!.parentElement!.getBoundingClientRect().height : null,
      iconOffset: (svg.top + svg.bottom - rect.top - rect.bottom) / 2 };
  });
}

for (const width of [1440, 390]) test(`header baselines and content width ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    localStorage.setItem("hey-my-buddy.console.theme", "dark");
    localStorage.setItem("hey-my-buddy.language", "zh");
  });
  let history = true;
  await page.route(`**/api/macro-tasks/${macro}/goals`, route => route.fulfill({ json: { macroTaskId: macro, goals: history ? goals : [goals[0]] } }));
  await open(page, `#tasks/${macro}`);
  const header = page.locator('[data-slot="macro-overview-header"]');
  const identifier = header.locator('[data-slot="copyable-identifier"]');
  const id = await identifierGeometry(identifier);
  await mkdir(captures, { recursive: true });
  await page.screenshot({ path: `${captures}/after-${width}-header.png` });
  expect(id.factHeight).toBe(20);
  expect(id.height).toBeGreaterThanOrEqual(24);
  expect(Math.abs(id.labelBaseline! - id.valueBaseline)).toBeLessThanOrEqual(1);
  expect(id.neighbours.length).toBeGreaterThan(0);
  for (const y of id.neighbours) expect(Math.abs(y - id.valueBaseline)).toBeLessThanOrEqual(1);
  expect(Math.abs(id.iconOffset)).toBeLessThanOrEqual(1);
  const measurements = { id, cards: [] as unknown[] };
  for (const withHistory of [true, false]) {
    history = withHistory;
    await header.locator('[data-slot="macro-goal-trigger"]').click();
    const card = page.locator('[data-slot="macro-goal-card"]');
    await expect(card.locator(".goal-history")).toHaveCount(withHistory ? 1 : 0);
    await settle(page);
    const size = await card.evaluate(card => {
      const main = card.querySelector(".goal-main")!, prose = card.querySelector(".goal-body")!;
      const history = card.querySelector(".goal-history");
      const right = history ?? prose;
      const frame = card.getBoundingClientRect(), edge = right.getBoundingClientRect();
      const style = getComputedStyle(main);
      return { rightGap: frame.right - edge.right, expectedGap: 1 + (right === prose ? parseFloat(style.paddingRight) : 0),
        mainGap: (history?.getBoundingClientRect().left ?? frame.right - 1) - prose.getBoundingClientRect().right,
        width: frame.width, proseWidth: prose.getBoundingClientRect().width,
        fontSize: getComputedStyle(prose).fontSize, lineHeight: getComputedStyle(prose).lineHeight,
        left: frame.left, right: frame.right };
    });
    measurements.cards.push({ withHistory, ...size });
    await page.screenshot({ path: `${captures}/after-${width}-${withHistory ? "history" : "single"}.png` });
    expect(Math.abs(size.rightGap - size.expectedGap)).toBeLessThanOrEqual(1);
    expect(Math.abs(size.mainGap - 24)).toBeLessThanOrEqual(1);
    expect(size.proseWidth).toBeLessThanOrEqual(38 * 14.5);
    if (width === 1440) expect(size.proseWidth).toBe(38 * 14.5);
    expect(size.fontSize).toBe("14.5px"); expect(size.lineHeight).toBe("26.825px");
    const bounds = (await header.boundingBox())!;
    expect(size.left).toBeGreaterThanOrEqual(bounds.x);
    expect(size.right).toBeLessThanOrEqual(bounds.x + bounds.width);
    await page.keyboard.press("Escape");
  }
  await writeFile(`${captures}/after-${width}-measurements.json`, JSON.stringify(measurements, null, 2));
});

for (const width of [1440, 390]) test(`shared identifiers in detail header, turn and acceptance ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    localStorage.setItem("hey-my-buddy.console.theme", "dark");
    localStorage.setItem("hey-my-buddy.language", "zh");
  });
  await page.route("**/api/command", async route => {
    const response = await route.fetch(); const data = await response.json();
    if (route.request().postDataJSON().operation === "workflow_get") {
      for (const turn of data.result.turns) turn.sessionId = "synthetic-session-11111111-1111-4111-8111-111111111111";
    }
    await route.fulfill({ response, json: data });
  });
  await open(page, `#tasks/${macro}/preview-run-a1/3`);
  const header = page.locator('[data-slot="detail-header"]');
  const headerId = header.locator('[data-slot="copyable-identifier"]');
  await expect(headerId).toBeVisible();
  expect(Math.abs((await identifierGeometry(headerId)).iconOffset)).toBeLessThanOrEqual(1);
  const turn = page.locator('[data-slot="detail-band"] [data-slot="copyable-identifier"]');
  await expect(turn).toBeVisible();
  const turnId = await identifierGeometry(turn);
  expect(Math.abs(turnId.iconOffset)).toBeLessThanOrEqual(1);
  expect(Math.abs(turnId.labelBaseline! - turnId.valueBaseline)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: `${captures}/after-${width}-turn.png` });
  await page.locator('[data-slot="detail-strip"]').getByRole("button", { name: /^已验收(?: ·|$)/ }).click();
  const pane = page.locator('[data-slot="detail-pane"]');
  await pane.getByRole("button", { name: /·.*文件/ }).first().click();
  const commit = pane.locator('dl [data-slot="copyable-identifier"]');
  await expect(commit).toBeVisible();
  const commitId = await identifierGeometry(commit);
  expect(Math.abs(commitId.iconOffset)).toBeLessThanOrEqual(1);
  expect(Math.abs(commitId.labelBaseline! - commitId.valueBaseline)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: `${captures}/after-${width}-acceptance.png` });
});
