import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { Locator, Page } from "@playwright/test";
import { expect } from "@playwright/test";
import type { MacroTaskTimeline } from "../src/macro-task-types";
import { expectNoSidewaysScroll, open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const run = "preview-run-a1";
const late = "strip-inquiry-23";
const shots = fileURLToPath(new URL("../../../tmp/strip-linking", import.meta.url));
const strip = (page: Page) => page.locator('[data-slot=detail-strip]');
const item = (page: Page, key: string) => page.locator(`[data-slot=detail-pane] [data-item-key="${key}"]`);
const marker = (page: Page, key: string) => strip(page).locator(`[data-key="${key}"]`);

/** The real preview parsers read a long turn with matching timeline and message facts. */
async function longTurn(page: Page) {
  const at = (index: number) => new Date(Date.parse("2026-09-27T06:06:00Z") + index * 75_000).toISOString();
  await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
    const response = await route.fetch();
    const timeline = await response.json() as MacroTaskTimeline;
    const span = timeline.spans.find(span => span.spanId === "execution:att-a1-1")!;
    span.inquiries = Array.from({ length: 24 }, (_, index) => ({ inquiryId: `strip-inquiry-${index}`, createdAt: at(index), state: "answered" }));
    await route.fulfill({ response, json: timeline, headers: { ...response.headers(), date: new Date(timeline.observedAt).toUTCString() } });
  });
  await page.route("**/api/command", async route => {
    const request = route.request().postDataJSON();
    if (request.operation !== "message_list" || request.params.runId !== run) return route.continue();
    const response = await route.fetch();
    const data = await response.json();
    const first = data.result.messages.find((message: { inquiryId: string }) => message.inquiryId === "inq-a1-1");
    data.result.messages = Array.from({ length: 24 }, (_, index) => ({ ...first,
      messageId: `stored-strip-${index}`, inquiryId: `strip-inquiry-${index}`, createdAt: at(index), deliveredAt: at(index),
      question: `合成问询 ${index + 1} / Synthetic inquiry ${index + 1}: 请核对本回合的步骤、检查结果与产物。Check this turn's steps, checks and artifacts.`,
      answer: { ...first.answer, at: at(index), text: `合成答复 ${index + 1} / Synthetic answer ${index + 1}: 已核对相关记录。The recorded evidence has been checked.` },
    })).reverse();
    await route.fulfill({ response, json: data });
  });
}

async function inPane(target: Locator) {
  await expect.poll(() => target.evaluate(node => {
    // Content scrolls; step titles and waits stay in the pane's fixed header.
    const frame = node.closest('[data-slot=scroll-area-viewport]') ?? node.closest('[data-slot=detail-pane]');
    const view = frame!.getBoundingClientRect();
    const box = node.getBoundingClientRect();
    return box.top >= view.top - 1 && box.bottom <= view.bottom + 1;
  })).toBe(true);
}

async function markInStrip(page: Page) {
  await expect.poll(() => strip(page).locator('[data-slot=detail-strip-scroll]').evaluate(view => {
    const frame = view.getBoundingClientRect();
    const mark = view.querySelector('[data-slot=selected-item]')!.getBoundingClientRect();
    return mark.left >= frame.left - 1 && mark.right <= frame.right + 1;
  })).toBe(true);
}

for (const theme of ["dark", "light"]) for (const locale of ["zh-CN", "en-US"]) for (const width of [390, 1280, 1440]) {
  test.describe(`strip linking ${theme} ${locale} ${width}px`, () => {
    test.use({ locale, viewport: { width, height: 1000 }, reducedMotion: "reduce" });
    test("links late inquiries, waits and acceptance without moving the layout", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await longTurn(page);
      await open(page, `#tasks/${macro}/${run}/3`);
      const first = item(page, "inquiry:strip-inquiry-0");
      const last = item(page, `inquiry:${late}`);
      await expect(last).toBeAttached();
      await page.evaluate(() => document.fonts.ready);
      const ruler = strip(page).locator('[data-slot=zoom-ruler]:visible');
      for (let index = 0; index < 8 && await marker(page, `inquiry:${late}`).count() === 0; index++) await ruler.press("+");
      const lateMarker = marker(page, `inquiry:${late}`);
      await expect(lateMarker).toHaveAttribute("tabindex", "0");
      const geometry = () => page.locator('[data-slot=detail-header], [data-slot=detail-step-band], [data-slot=detail-pane]').evaluateAll(nodes => nodes.map(node => {
        const box = node.getBoundingClientRect();
        return { top: box.top, left: box.left, width: box.width, height: box.height };
      }));
      const before = await geometry();
      const contentHeight = await last.evaluate(node => node.closest('[data-slot=scroll-area-viewport]')!.scrollHeight);
      expect(await last.evaluate(node => node.getBoundingClientRect().top > node.closest('[data-slot=scroll-area-viewport]')!.getBoundingClientRect().bottom)).toBe(true);
      await lateMarker.click();
      await expect(last).toHaveAttribute("data-highlighted", "true");
      await expect(page).toHaveURL(new RegExp(`/${run}/3$`));
      await inPane(last);
      await markInStrip(page);
      expect(await geometry()).toEqual(before);
      expect(await last.evaluate(node => node.closest('[data-slot=scroll-area-viewport]')!.scrollHeight)).toBe(contentHeight);
      await expectNoSidewaysScroll(page);
      if (locale === "zh-CN" && width !== 1280) {
        await mkdir(shots, { recursive: true });
        await page.screenshot({ path: `${shots}/inquiry-${theme}-${width}.png` });
      }

      // A passing pointer, and a plain focus, move nothing on the other surface; only a click selects.
      await first.hover();
      const firstMarker = marker(page, "inquiry:strip-inquiry-0");
      await expect(firstMarker.locator('[data-slot=fragment-focus]')).toHaveCSS("opacity", "0");
      await expect(lateMarker).toHaveAttribute("aria-pressed", "true");
      await first.focus();
      await page.mouse.move(0, 0);
      await expect(firstMarker.locator('[data-slot=fragment-focus]')).toHaveCSS("opacity", "0");
      // The selected mark carries exactly one frame, with no focus frame stacked on it.
      await expect(lateMarker.locator('[data-slot=selected-item]')).toHaveCount(1);
      await expect(lateMarker.locator('[data-slot=fragment-focus]')).toHaveCSS("opacity", "0");
      await first.click();
      await expect(first).toHaveAttribute("data-highlighted", "true");
      await expect(firstMarker).toHaveAttribute("aria-pressed", "true");
      await strip(page).locator('[data-slot=detail-strip-scroll]').evaluate(view => { view.scrollLeft = 0; });
      const minimumScroll = await strip(page).locator('[data-slot=detail-strip-scroll]').evaluate((view, key) => {
        const box = view.querySelector(`[data-key="${key}"] [data-slot=inquiry-mark]`)!.getBoundingClientRect();
        return Math.max(0, (box.left + box.right) / 2 + 9 - view.getBoundingClientRect().right);
      }, `inquiry:${late}`);
      await last.getByRole("button").first().click();
      await expect(last).toHaveAttribute("data-highlighted", "true");
      await markInStrip(page);
      await inPane(last);
      expect(await strip(page).locator('[data-slot=detail-strip-scroll]').evaluate(view => view.scrollLeft)).toBeCloseTo(minimumScroll, 0);
      await expect(firstMarker.locator('[data-slot=fragment-focus]')).toHaveCSS("opacity", "0");
      expect(await geometry()).toEqual(before);
      await strip(page).locator('[data-slot=detail-strip-scroll]').evaluate(view => { view.scrollLeft = 0; });
      await last.getByRole("button").first().click();
      await markInStrip(page);

      // Enter and Space use the exact marker, while arrows still choose adjacent steps.
      await lateMarker.focus();
      await lateMarker.press("ArrowRight");
      await expect(page).toHaveURL(new RegExp(`/${run}/4$`));
      await lateMarker.press(" ");
      await expect(last).toHaveAttribute("data-highlighted", "true");
      await inPane(last);
      await lateMarker.press("Enter");
      await expect(page).toHaveURL(new RegExp(`/${run}/3$`));

      // The Host wait has its own overview row and retains its exact span identity.
      const waitKey = "span:host:req-a1-1";
      await marker(page, waitKey).click();
      await expect(item(page, waitKey)).toHaveAttribute("data-highlighted", "true");
      await item(page, waitKey).getByRole("button").click();
      await markInStrip(page);
      const queueKey = "span:queue:att-a1-2";
      await marker(page, queueKey).click();
      await expect(item(page, queueKey)).toHaveAttribute("data-highlighted", "true");
      await item(page, queueKey).getByRole("button").click();
      await markInStrip(page);
      const settleKey = `settle:${run}`;
      await marker(page, settleKey).click();
      await expect(item(page, settleKey)).toHaveAttribute("data-highlighted", "true");
      await item(page, settleKey).getByRole("button").click();
      await markInStrip(page);

      const acceptance = strip(page).locator('[data-key^="event:"]', { has: page.locator('[data-slot=accept-mark]') });
      await expect(acceptance).toHaveCount(1);
      const acceptanceKey = (await acceptance.getAttribute("data-key"))!;
      await acceptance.click();
      await expect(item(page, acceptanceKey)).toHaveAttribute("data-highlighted", "true");
      await inPane(item(page, acceptanceKey));
      await item(page, acceptanceKey).getByRole("button").click();
      await markInStrip(page);
      if (locale === "zh-CN" && width !== 1280) await page.screenshot({ path: `${shots}/acceptance-${theme}-${width}.png` });
    });
  });
}

for (const motion of ["reduce", "no-preference"] as const) test.describe(`strip linking motion ${motion}`, () => {
  test.use({ viewport: { width: 390, height: 1000 }, reducedMotion: motion });
  test("uses the requested scrolling behavior in both directions", async ({ page }) => {
    await page.addInitScript(() => {
      const calls: { key: string; behavior?: string }[] = [];
      Object.assign(window, { stripScrollCalls: calls });
      const intoView = Element.prototype.scrollIntoView;
      Element.prototype.scrollIntoView = function (options) {
        calls.push({ key: this.getAttribute("data-item-key") ?? "", behavior: typeof options === "object" ? options.behavior : undefined });
        intoView.call(this, options);
      };
      const scroll = Element.prototype.scrollTo;
      Element.prototype.scrollTo = function (...args: [ScrollToOptions?] | [number, number]) {
        if (this.getAttribute("data-slot") === "detail-strip-scroll") calls.push({ key: "strip", behavior: typeof args[0] === "object" ? args[0].behavior : undefined });
        Reflect.apply(scroll, this, args);
      };
    });
    await longTurn(page);
    await open(page, `#tasks/${macro}/${run}/3`);
    await expect(item(page, `inquiry:${late}`)).toBeAttached();
    const ruler = strip(page).locator('[data-slot=zoom-ruler]:visible');
    for (let index = 0; index < 8 && await marker(page, `inquiry:${late}`).count() === 0; index++) await ruler.press("+");
    await marker(page, `inquiry:${late}`).click();
    await inPane(item(page, `inquiry:${late}`));
    await item(page, "inquiry:strip-inquiry-0").getByRole("button").first().click();
    await markInStrip(page);
    const calls = await page.evaluate(() => (window as unknown as { stripScrollCalls: { key: string; behavior: string }[] }).stripScrollCalls);
    const expected = motion === "reduce" ? "instant" : "smooth";
    expect(calls.filter(call => call.key === `inquiry:${late}`).map(call => call.behavior)).toContain(expected);
    expect(calls.filter(call => call.key === "strip").map(call => call.behavior)).toContain(expected);
    expect(calls.filter(call => call.key === "strip" || call.key === `inquiry:${late}`).every(call => call.behavior === expected)).toBe(true);
  });
});
