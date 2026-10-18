import { expect, type Locator, type Page } from "@playwright/test";
import type { MacroTaskTimeline, TimelineSpan } from "../src/macro-task-types";
import { screenshots as shots, expectNoSidewaysScroll, open, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";

/** The approved drawing's time distribution, using the service's exact DTO. */
function comparison(timeline: MacroTaskTimeline): MacroTaskTimeline {
  const at = (clock: string) => new Date(Date.parse(`2026-09-27T${clock}:00.000Z`) + 29 * 60_000).toISOString();
  const ids = timeline.rows.slice(0, 5).map(row => row.runId);
  const titles = ["打底：样式与组件", "设置页", "Buddy 页：模型分区", "时间轴比例尺", "标签的对比度"];
  const source = timeline.spans.find(span => span.kind === "execution")!;
  const router = timeline.spans.find(span => span.kind === "routing")!;
  const piece = (row: number, key: string, kind: TimelineSpan["kind"], start: string, end: string | null, extra: Partial<TimelineSpan> = {}): TimelineSpan => ({
    ...source, spanId: key, runId: ids[row]!, buddy: timeline.rows[row]!.buddy, kind, startAt: at(start), endAt: end ? at(end) : null,
    state: end ? "finished" : "executing", shutdownConfirmed: end ? true : false, uncertain: false, clockSkew: false,
    resultStatus: end ? "ok" : undefined, error: undefined, expectedSeconds: kind === "execution" ? 1200 : null,
    reminders: null, inquiries: kind === "execution" ? [] : null, ...extra,
  });
  timeline.observedAt = at("09:31");
  timeline.rows = timeline.rows.slice(0, 5).map((row, index) => ({ ...row, title: titles[index]!, titleSource: "title", parentRunId: null, rootRunId: row.runId, depth: 0, kind: "micro-task",
    state: index === 0 ? "accepted" : index === 3 ? "executing" : "delivered", status: index === 3 ? "running" : "completed", category: index === 0 ? "ended" : index === 3 ? "active" : "review",
    shutdownConfirmed: index !== 3, acceptedAt: index === 0 ? at("09:08") : null, reviewRejectedAt: null, reviewRejectionNote: null }));
  timeline.spans = [piece(0, "foundation", "execution", "06:00", "06:04"),
    piece(1, "failed", "execution", "06:02", "06:10", { resultStatus: "failed" }), piece(1, "retry", "execution", "06:45", "06:47"),
    piece(2, "models", "execution", "06:46", "06:50"),
    piece(3, "queued", "queue", "09:04", "09:05", { state: "admitted" }),
    piece(3, "router", "routing", "09:05", "09:06", { routing: router.routing }),
    piece(3, "live", "execution", "09:06", null, { reminders: { overdue: true, possiblyStuck: true, lastNativeEventAt: at("09:12"), toolInProgress: false } }),
    piece(4, "repair", "execution", "09:15", "09:22", { inquiries: [{ inquiryId: "comparison-question", createdAt: at("09:19"), author: "preview-host", state: "answered" }] })];
  const event = timeline.events[0]!;
  timeline.events = [{ ...event, seq: 1, runId: ids[0]!, kind: "accept", at: at("09:08"), verdict: "accepted", note: "Review passed" }];
  timeline.totals = { rows: 5, allRows: 5, spans: timeline.spans.length, events: timeline.events.length };
  timeline.macroTask = { ...timeline.macroTask, title: "控制台前端重建", rootRunIds: ids, counts: { ...timeline.macroTask.counts, roots: 5, helpers: 0, active: 1, host: 0, review: 3, accepted: 1, ended: 1 } };
  return timeline;
}

/** Check rendered SVG glyphs, including labels whose width changes with language or font. */
async function expectSvgLabelsWholeAndApart(svg: Locator, slots: string): Promise<void> {
  const result = await svg.evaluate((node, wanted) => {
    const frame = node.getBoundingClientRect();
    const labels = [...node.querySelectorAll<SVGTextElement>(wanted)].map(element => {
      const rect = element.getBoundingClientRect();
      return { text: element.textContent ?? "", left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
    });
    const outside = labels.filter(label => label.right - label.left <= 0 || label.bottom - label.top <= 0
      || label.left < frame.left - .75 || label.right > frame.right + .75
      || label.top < frame.top - .75 || label.bottom > frame.bottom + .75);
    const overlaps: string[] = [];
    for (let first = 0; first < labels.length; first += 1) for (let second = first + 1; second < labels.length; second += 1) {
      const a = labels[first]!, b = labels[second]!;
      if (a.left < b.right - .5 && b.left < a.right - .5 && a.top < b.bottom - .5 && b.top < a.bottom - .5)
        overlaps.push(`${a.text} / ${b.text}`);
      else if (a.top < b.bottom - .5 && b.top < a.bottom - .5 && Math.max(b.left - a.right, a.left - b.right) < 11.5) overlaps.push(`${a.text} / ${b.text}: gap below 12px`);
    }
    const marks = [...node.querySelectorAll('[data-slot="inquiry-mark"], [data-slot="expected-mark"], [data-slot="event-mark"], [data-slot="accept-mark"], [data-slot="waiting-diamond"], [data-slot="unconfirmed-end"]')].map(mark => mark.getBoundingClientRect());
    for (const a of labels) for (const b of marks) {
      if (a.left < b.right - .5 && b.left < a.right - .5 && a.top < b.bottom - .5 && b.top < a.bottom - .5)
        overlaps.push(`${a.text} / mark`);
    }
    return { count: labels.length, outside, overlaps };
  }, slots);
  expect(result.count, "there are labels to measure").toBeGreaterThan(0);
  expect(result.outside, "SVG labels leave their own frame").toEqual([]);
  expect(result.overlaps, "SVG labels overlap in one track or axis").toEqual([]);
}

async function expectDrawingGeometry(page: Page): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  const axis = page.locator('[data-slot="timeline-axis"]');
  await expectSvgLabelsWholeAndApart(axis, '[data-slot="axis-label"], [data-slot="gap-label"]');
  const tracks = page.locator('[data-slot="timeline-track"]');
  expect(await tracks.count()).toBeGreaterThan(0);
  for (const track of await tracks.all()) {
    if (await track.locator('[data-slot="track-label"]').count())
      await expectSvgLabelsWholeAndApart(track, '[data-slot="track-label"]');
  }
  await expectNoSidewaysScroll(page);
}

for (const locale of ["zh-CN", "en-US"] as const) for (const theme of ["light", "dark"] as const) for (const width of [1440, 390]) {
  test.describe(`timeline drawing ${locale} ${theme} ${width}px`, () => {
    test.use({ locale, viewport: { width, height: width === 390 ? 844 : 1000 }, reducedMotion: "reduce" });

    test("draws complete, separate labels and readable evidence", async ({ page }) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      // Keep the normal preview's unknown stop in a separate check. This
      // comparison fixture has two idle intervals, as the approved drawing does.
      await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
        const response = await route.fetch();
        await route.fulfill({ response, json: comparison(await response.json() as MacroTaskTimeline) });
      });
      await open(page, `#tasks/${macro}`);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await page.locator(`[data-macro-task-id="${macro}"]`).waitFor({ state: "attached" });
      await expect(page.locator('[data-slot="timeline-axis"]')).toHaveCount(1);
      await expect(page.locator('[data-slot="timeline-track"]')).toHaveCount(5);

      const drawing = page.locator('[data-slot="timeline-tracks"]');
      for (const slot of ["axis-break-mark", "execution-bar", "routing-bar", "silent-tail", "expected-mark",
        "inquiry-mark", "waiting-diamond", "accept-mark"]) {
        expect(await drawing.locator(`[data-slot="${slot}"]`).count(), `${slot} is drawn`).toBeGreaterThan(0);
      }
      if (width === 1440) await expect(drawing.locator('[data-slot="track-label"]').filter({ hasText: locale === "zh-CN" ? /可能卡住|无动静/ : /Possibly stuck|Silent/ })).toHaveCount(1);
      await expect(drawing.locator('[data-slot="gap-label"]')).toHaveText(locale === "zh-CN" ? ["35 分", "2 时 14 分"] : ["35 min", "2 h 14 min"]);
      await expect(page.locator('[data-slot="timeline-row-name"][data-run-id="preview-run-a1"]:visible [data-slot="fixed-by"]')).toHaveText("↩ 2");
      for (const run of ["preview-run-a2", "preview-run-a3"])
        await expect(page.locator(`[data-slot="timeline-row-name"][data-run-id="${run}"]:visible`)).toHaveAttribute("data-fixes", "preview-run-a1");

      const ended = drawing.locator('[data-slot="execution-bar"][data-outcome="finished"]');
      expect(await ended.count()).toBeGreaterThan(1);
      const colors = new Set(await ended.evaluateAll(bars => bars.map(bar => getComputedStyle(bar).fill)));
      expect(colors.size).toBeGreaterThan(1);
      // Every shown duration belongs inside its bar, even beside an inquiry.
      const durationPositions = await drawing.locator('[data-slot="timeline-track"]').evaluateAll(tracks => tracks.flatMap(track => {
        const bars = [...track.querySelectorAll('[data-slot="execution-bar"], [data-slot="routing-bar"], [data-slot="silent-tail"]')].map(bar => bar.getBoundingClientRect());
        return [...track.querySelectorAll('[data-slot="track-label"]')].filter(label => /^(\d+\s*(分|时|秒|min|h|s)\s*)+$/.test(label.textContent ?? "")).flatMap(label => {
          const box = label.getBoundingClientRect();
          if (!bars.some(bar => box.top >= bar.top && box.bottom <= bar.bottom)) return [];
          return [{ text: label.textContent, inside: bars.some(bar => box.left >= bar.left && box.right <= bar.right) }];
        });
      }));
      expect(durationPositions.length).toBeGreaterThan(0);
      expect(durationPositions.filter(label => !label.inside)).toEqual([]);
      await expectDrawingGeometry(page);
      // The acceptance mark keeps its word for the readout; the track beside it stays quiet.
      const acceptedTrack = page.locator('[data-slot="timeline-track"]').first();
      const acceptedMark = acceptedTrack.locator('[data-slot="accept-mark"]');
      await expect(acceptedMark).toHaveCount(1);
      await expect(acceptedTrack.locator('[data-slot="track-label"]').filter({ hasText: locale === "zh-CN" ? "已验收" : "Accepted" })).toHaveCount(0);
      await acceptedMark.hover();
      await expect(page.locator('[data-slot="timeline-status"]')).toContainText(locale === "zh-CN" ? "已验收" : "Accepted");
      await page.mouse.move(0, 0);
      const now = page.locator('[data-slot="timeline-axis"] [data-slot="axis-label"].fill-live');
      await expect(now).toContainText(locale === "zh-CN" ? "现在" : "Now");
      if (locale === "zh-CN") {
        await page.screenshot({ path: `${shots}/drawing-${theme}-${width}.png` });
        if (width === 390) {
          await drawing.evaluate(node => { node.scrollTop = node.scrollHeight; });
          await page.screenshot({ path: `${shots}/drawing-${theme}-${width}-bottom.png` });
          await drawing.evaluate(node => { node.scrollTop = 0; });
        }
      }

      // Moving over the compressed interval reads its real endpoints and duration.
      const breakMark = drawing.locator('[data-slot="axis-break-mark"]').first();
      await breakMark.scrollIntoViewIfNeeded();
      const mark = (await breakMark.boundingBox())!;
      const axis = (await page.locator('[data-slot="timeline-axis"]').boundingBox())!;
      await page.mouse.move(mark.x + mark.width / 2, axis.y + 12);
      await expect(page.locator('[data-slot="timeline-pointer"]')).toHaveCount(1);
      await expect(page.locator('[data-slot="pointer-clock"]')).toHaveCount(0);
      const status = page.locator('[data-slot="timeline-status"]');
      await expect(status).toContainText(locale === "zh-CN"
        ? /空闲\s+\d{2}:\d{2}–\d{2}:\d{2}\s+·\s+\S+/
        : /Idle\s+\d{2}:\d{2}–\d{2}:\d{2}\s+·\s+\S+/);
      if (locale === "zh-CN" && theme === "light" && width === 1440)
        await page.screenshot({ path: `${shots}/drawing-break-readout.png` });
      await expectNoSidewaysScroll(page);
    });
  });
}

test.describe("incomplete timeline drawing", () => {
  test.use({ locale: "en-US", viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });

  test("does not fold unknown time while keeping its other evidence", async ({ page }) => {
    await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
      const response = await route.fetch();
      const timeline = await response.json() as MacroTaskTimeline;
      timeline.truncated.spans = true;
      timeline.scopeComplete = false;
      await route.fulfill({ response, json: timeline });
    });
    await open(page, `#tasks/${macro}`);
    await expect(page.locator('[data-slot="timeline-axis"]')).toHaveCount(1);
    await expect(page.locator('[data-slot="axis-break-mark"]')).toHaveCount(0);
    await expect(page.locator('[data-slot="gap-label"]')).toHaveCount(0);
    await expect(page.locator('[data-slot="execution-bar"]')).not.toHaveCount(0);
    await expect(page.locator('[data-slot="unconfirmed-end"]')).toHaveCount(1);
    await expect(page.locator('[data-slot="timeline-status"]')).toContainText("time is not compressed");
    await expectDrawingGeometry(page);
  });
});


test.describe("unreached expected duration", () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 1000 } });
  test("keeps the timeline at Now and the expectation in its readout", async ({ page }) => {
    await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
      const response = await route.fetch();
      const timeline = await response.json() as MacroTaskTimeline;
      const live = timeline.spans.find(span => span.spanId === "execution:att-a3-1")!;
      live.expectedSeconds = 14400; live.reminders!.overdue = false;
      await route.fulfill({ response, json: timeline });
    });
    await open(page, `#tasks/${macro}`);
    const track = page.locator('[data-slot="timeline-track"]').filter({ has: page.locator('[data-key="span:execution:att-a3-1"]') });
    await expect(track.locator('[data-slot="expected-mark"]')).toHaveCount(0);
    const nowX = Number(await track.locator('[data-slot="timeline-now"]').getAttribute("x1"));
    const frame = await track.getAttribute("viewBox");
    expect(Number(frame!.split(" ")[2]) - 16).toBeCloseTo(nowX);
    await track.locator('[data-key="span:execution:att-a3-1"]').hover();
    await expect(page.locator('[data-slot="timeline-status"]')).toContainText("Expected 4 h");
    await expectDrawingGeometry(page);
  });
});

test.describe("inquiry beside a short wait", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  test("keeps twelve pixels between the inquiry label and the following waiting duration, or omits it", async ({ page }) => {
    await page.route(`**/api/macro-tasks/${macro}/timeline*`, async route => {
      const response = await route.fetch();
      const data = comparison(await response.json() as MacroTaskTimeline);
      const last = data.spans.find(span => span.spanId === "repair")!;
      last.endAt = last.inquiries![0]!.createdAt;
      data.spans.push({ ...last, spanId: "short-wait", kind: "host", startAt: last.endAt, endAt: new Date(Date.parse(last.endAt!) + 60000).toISOString(), state: "approved", inquiries: null, expectedSeconds: null, reminders: null });
      data.totals.spans = data.spans.length;
      data.rows[4] = { ...data.rows[4]!, state: "accepted", category: "ended", acceptedAt: null };
      await route.fulfill({ response, json: data });
    });
    await open(page, `#tasks/${macro}`);
    const track = page.locator('[data-slot=timeline-track]').last();
    await expect(track.locator('[data-slot=track-label][y="12"]').filter({ hasText: "问询" })).toHaveCount(1);
    await expectSvgLabelsWholeAndApart(track, '[data-slot=track-label]');
  });
});
