import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { contrastOf, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, test } from "./preview";

// The detail of a micro task on the synthetic preview. Macro task A holds a1 (accepted, two turns, a change
// of buddy, an answered inquiry and one that could not be delivered), a2 (waiting for its Host), a3 (running,
// silent for 19 minutes, with an inquiry delivered and not answered). Macro task B holds b2 (failed, concluded).
const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";

const languages = [
  {
    locale: "zh-CN", refresh: "重新读取", turn: (n: number) => `回合 ${n}`, route: "路由", delegate: "委派", acceptance: "验收",
    running: "进行中", stuck: "可能卡住", overdue: "已超过预期", waitingHost: "等待 Host", failed: "失败", mode: "快速",
    answered: "已答复", unavailable: "无法送达", delivered: "已送达，等待答复", conclusion: "Host 的结论", notCounted: "不计入验收",
    integration: "整合", reported: /上报 \d+ 分钟前/, handoff: "换手", unsure: "结束未确认", helperDone: "已完成", ask: "上一轮停下了吗？",
  },
  {
    locale: "en-US", refresh: "Read again", turn: (n: number) => `Turn ${n}`, route: "Route", delegate: "Delegate", acceptance: "Acceptance",
    running: "Running", stuck: "Possibly stuck", overdue: "Overdue", waitingHost: "Waiting for Host", failed: "Failed", mode: "Fast",
    answered: "Answered", unavailable: "Could not be delivered", delivered: "Delivered, awaiting answer", conclusion: "Host's conclusion", notCounted: "not counted as accepted",
    integration: "Integration", reported: /Reported \d+ min ago/, handoff: "Handoff", unsure: "Stop not confirmed", helperDone: "Done", ask: "上一轮停下了吗？",
  },
];

const detail = (page: Page) => page.locator("[data-slot=task-detail]");
const rail = (page: Page) => page.locator("[data-slot=detail-rail]");
const pane = (page: Page) => page.locator("[data-slot=detail-pane]");
/** The scrolled content of the pane: the frame its parts have to stay inside, however far it scrolls. */
const content = (page: Page) => pane(page).locator("div.w-0.min-w-full").first();

/** Every part of the open step is whole, apart from its neighbours, and drawn in full. */
async function expectDetailWhole(page: Page, { skipTitle = false } = {}) {
  const header = detail(page).locator("[data-slot=detail-header]");
  const line = header.locator(":scope > div:first-child");
  await expectWholeAndApart(header, line.locator(":scope > *"));
  // The title is cut with an ellipsis by design; everything else in the head is written whole.
  await expectTextUncut(line.locator("button, dt, dd, span:not([data-slot=state-mark])").filter({ hasNot: page.locator("h2") }));
  await expectWholeAndApart(rail(page), rail(page).locator("li"));
  await expectTextUncut(rail(page).locator("li span"));
  await expectWholeAndApart(content(page), content(page).locator(":scope > *"));
  await expectTextUncut(pane(page).locator("[data-slot=detail-band] span, [data-slot=detail-live] span"));
  await expectNoSidewaysScroll(page);
  const sideways = await pane(page).locator("[data-slot=scroll-area-viewport]").evaluate(node => node.scrollWidth - node.clientWidth);
  expect(sideways, "the pane scrolls sideways").toBeLessThanOrEqual(0);
}

/** The title takes its own line on narrow screens; status stays beside refresh. */
async function expectOneLineHead(page: Page, refresh: string, narrow: boolean) {
  const header = detail(page).locator("[data-slot=detail-header]");
  const [title, figures, status, again, mark] = await Promise.all([
    header.locator("h2").boundingBox(), header.locator("dl").boundingBox(),
    header.locator("[data-slot=detail-status]").boundingBox(), header.getByRole("button", { name: refresh }).boundingBox(),
    header.locator("[data-slot=state-mark]").first().boundingBox(),
  ]);
  const centre = (box: { y: number; height: number }) => box.y + box.height / 2;
  expect(Math.abs(centre(status!) - centre(again!)), "the state stays beside refresh").toBeLessThan(10);
  expect(Math.abs(centre(mark!) - centre(title!)), "the mark stays beside the title").toBeLessThan(10);
  if (narrow) {
    expect(figures!.y, "facts follow the title").toBeGreaterThan(title!.y);
    expect(status!.y, "state and refresh follow the facts").toBeGreaterThanOrEqual(figures!.y + figures!.height);
  } else {
    expect(Math.abs(centre(again!) - centre(figures!)), "wide, refresh shares the facts line").toBeLessThan(10);
    expect(again!.x, "wide, refresh is at the right end").toBeGreaterThan(figures!.x);
    expect(Math.abs(centre(title!) - centre(figures!)), "wide, the head is one line").toBeLessThan(10);
  }
}

/** A change of address inside the page, as a reader makes it: the page does not reload. */
async function go(page: Page, hash: string): Promise<void> {
  await page.evaluate(next => { window.location.hash = next; }, hash);
}

const step = (page: Page, text: string | RegExp) => rail(page).locator("button").filter({ hasText: text });

for (const { locale, refresh, turn, route, delegate, acceptance, running, stuck, overdue, waitingHost, failed, mode, answered, unavailable, delivered, conclusion, notCounted, integration, reported, handoff, unsure, helperDone, ask } of languages)
  for (const width of [1440, 1280, 390]) {
    test.describe(`the detail of a micro task in ${locale} at ${width}px`, () => {
      // Colours are read at rest: under reduced motion the page makes no transitions.
      test.use({ locale, viewport: { width, height: width === 390 ? 844 : 900 }, reducedMotion: "reduce" });

      test("opens from the timeline, by address, with Escape, and goes back and forward by the browser's history", async ({ page }) => {
        await open(page, `#tasks/${A}`);
        // Opened from a span of the timeline, the detail opens on the turn that span belongs to.
        const span = page.locator('[data-slot=timeline-tracks] [data-key="span:execution:att-a1-2"]');
        await span.waitFor();
        await span.click();
        await expect(detail(page)).toBeVisible();
        await expect(page).toHaveURL(new RegExp(`#tasks/${A}/preview-run-a1/7$`));
        await expect(rail(page).locator("[aria-current=step]")).toContainText(turn(2));
        // The macro task's own header and row labels stay while the time tracks give way to the detail.
        await expect(page.locator("[data-slot=macro-overview-header] h2")).toBeVisible();
        await expect(page.locator('[data-slot=task-names] [data-key="row:preview-run-a1"]')).toBeVisible();
        await expect(page.locator('[data-slot=timeline-tracks]')).toHaveAttribute("aria-hidden", "true");
        await page.goBack();
        await expect(page).toHaveURL(new RegExp(`#tasks/${A}$`));
        await expect(detail(page)).toHaveCount(0);
        await page.goForward();
        await expect(page).toHaveURL(new RegExp(`#tasks/${A}/preview-run-a1/7$`));
        await expect(rail(page).locator("[aria-current=step]")).toContainText(turn(2));
        // Choosing a step replaces the address it is on: back leaves the detail, not the step.
        await step(page, delegate).click();
        await expect(page).toHaveURL(new RegExp(`#tasks/${A}/preview-run-a1/1$`));
        await page.goBack();
        await expect(page).toHaveURL(new RegExp(`#tasks/${A}$`));
        await page.goForward();
        await expect(detail(page)).toBeVisible();
        // A hint open under the pointer takes the first Escape; with none open, Escape goes back.
        await page.mouse.move(0, 0);
        await page.keyboard.press("Escape");
        await expect(page).toHaveURL(new RegExp(`#tasks/${A}$`));
        await expect(detail(page)).toHaveCount(0);
      });

      test("opens a step by its address, the first choice when the address names none, and a failed read when it names no micro task", async ({ page }) => {
        await open(page, `#tasks/${A}/preview-run-a1/4`);
        await expect(rail(page).locator("[aria-current=step]")).toHaveAttribute("data-step", "4");
        await expect(pane(page)).toContainText(locale === "zh-CN" ? "批准" : "Approved");
        // No step in the address: the detail settles on the last turn and writes it into the address.
        await go(page, `#tasks/${A}/preview-run-a1`);
        await expect(rail(page).locator("[aria-current=step]")).toContainText(turn(2));
        await expect(page).toHaveURL(new RegExp(`#tasks/${A}/preview-run-a1/7$`));
        // A step that does not exist is replaced by the first choice.
        await go(page, `#tasks/${A}/preview-run-a1/99`);
        await expect(page).toHaveURL(new RegExp(`#tasks/${A}/preview-run-a1/7$`));
        // A micro task whose read is not what was asked for reads as a failure.
        await page.route("**/api/tasks/no-such-run", route => route.fulfill({ json: { runId: "another-run" } }));
        await go(page, `#tasks/${A}/no-such-run`);
        await expect(detail(page).getByRole("alert")).toBeVisible();
        await expect(detail(page).getByRole("button", { name: locale === "zh-CN" ? "重试读取" : "Retry" })).toBeVisible();
        await page.keyboard.press("Escape");
        await expect(page).toHaveURL(new RegExp(`#tasks/${A}$`));
      });

      test("hangs the rail below the selected row while the step fills the detail area", async ({ page }) => {
        await open(page, `#tasks/${A}/preview-run-a1/7`);
        await expect(detail(page)).toBeVisible();
        await expect(rail(page)).toBeVisible();
        await expect(detail(page).locator("[data-slot=detail-rail]")).toHaveCount(0);
        const [rowBox, railBox, paneBox, namesBox] = [
          (await page.locator('[data-slot=task-names] [data-key="row:preview-run-a1"]').boundingBox())!,
          (await rail(page).boundingBox())!,
          (await pane(page).boundingBox())!,
          (await page.locator('[data-slot=task-names]').boundingBox())!,
        ];
        expect(railBox.y).toBeGreaterThanOrEqual(rowBox.y + rowBox.height - 1);
        expect(Math.abs(railBox.x - rowBox.x)).toBeLessThanOrEqual(3);
        if (width > 760) {
          expect(paneBox.x).toBeGreaterThanOrEqual(railBox.x + railBox.width - 1);
        } else {
          expect(paneBox.y).toBeGreaterThanOrEqual(namesBox.y + namesBox.height - 1);
        }
        await expectDetailWhole(page, { skipTitle: true });
        await expectOneLineHead(page, refresh, width <= 760);
        // A step reached by the keyboard is the one shown.
        await step(page, delegate).focus();
        await page.keyboard.press("Enter");
        await expect(rail(page).locator("[aria-current=step]")).toHaveAttribute("data-step", "1");
        await expectDetailWhole(page, { skipTitle: true });
      });

      test("the delegation step: the task, its source, its helpers and its fixes", async ({ page }) => {
        await open(page, `#tasks/${A}/preview-run-a1/1`);
        await expect(pane(page).getByRole("heading", { name: delegate })).toBeVisible();
        await expect(pane(page).getByRole("button", { name: /preview-run-a1/ })).toBeVisible();
        await expect(pane(page).getByRole("button", { name: /schema/ })).toBeVisible();
        await expect(pane(page).getByText("↩")).toBeVisible();
        // A helper is written in the fixed words with a mark, not as the board names its state.
        const helper = pane(page).getByRole("button", { name: /schema/ }).locator("xpath=ancestor::li");
        await expect(helper).toContainText(helperDone);
        await expect(helper.locator("[data-slot=state-mark]")).toHaveCount(1);
        await expect(helper).not.toContainText(/delivered|succeeded|active/);
        await expectDetailWhole(page, { skipTitle: true });
        // A helper opens at its own address, inside the same macro task.
        await pane(page).getByRole("button", { name: /schema/ }).click();
        await expect(page).toHaveURL(new RegExp(`#tasks/${A}/preview-run-a1-h1(/\\d+)?$`));
      });

      test("the route step: the Router, its mode and what it chose", async ({ page }) => {
        await open(page, `#tasks/${A}/preview-run-a1/2`);
        const band = pane(page).locator("[data-slot=detail-band]");
        await expect(band).toContainText("Router");
        await expect(band).toContainText("gpt-6-luna");
        await expect(band).toContainText(mode);
        await expect(pane(page).locator("[data-slot=detail-thread]")).toContainText("deepseek-flash");
        await expectDetailWhole(page, { skipTitle: true });
      });

      test("a finished turn: the input, its inquiries and their answers, the result and the seal", async ({ page }) => {
        await open(page, `#tasks/${A}/preview-run-a1/3`);
        const thread = pane(page).locator("[data-slot=detail-thread]");
        await expect(thread).toContainText("目前读到哪个文件了？");
        await expect(thread).toContainText(answered);
        await expect(thread).toContainText("正在核对");
        await expect(thread).toContainText(unavailable);
        await expect(pane(page).locator("[data-slot=detail-band]")).toContainText("Worker");
        // The seal opens to its evidence.
        await expectDetailWhole(page, { skipTitle: true });
        // The second turn begins with the Host's continuation input, and ends in a seal that opens to its evidence.
        await step(page, turn(2)).click();
        await expect(pane(page).locator("[data-slot=detail-thread]")).toContainText("路径边界也核对一遍");
        const seal = pane(page).getByRole("button", { name: new RegExp(locale === "zh-CN" ? "封存" : "Seal") });
        await seal.click();
        await expect(pane(page).getByText("SHA-256").first()).toBeVisible();
        await expectDetailWhole(page, { skipTitle: true });
        await step(page, handoff).click();
        await expect(pane(page)).toContainText("deepseek-flash");
        await expectDetailWhole(page, { skipTitle: true });
      });

      test("an inquiry that names no attempt is shown in the turn that came after it", async ({ page }) => {
        await open(page, `#tasks/${A}/preview-run-a1/7`);
        await expect(pane(page).locator("[data-slot=detail-thread]")).toContainText(ask);
        await expectDetailWhole(page, { skipTitle: true });
        await step(page, turn(1)).click();
        await expect(pane(page).locator("[data-slot=detail-thread]")).not.toContainText(ask);
      });

      test("a stop that is not confirmed is written as not confirmed, in the turn and on the rail", async ({ page }) => {
        await open(page, `#tasks/${A}/preview-run-a1-h1-h1`);
        await expect(detail(page)).toBeVisible();
        const title = pane(page).locator("h3").first().locator("..");
        await expect(title).toContainText(unsure);
        await expect(title).not.toContainText(locale === "zh-CN" ? "已结束" : "Ended");
        await expect(rail(page).locator("[aria-current=step]")).toContainText(unsure);
        const mark = await rail(page).locator("[aria-current=step] i").evaluate(node => getComputedStyle(node).borderTopStyle);
        expect(mark).toBe("dashed");
        await expectDetailWhole(page, { skipTitle: true });
      });

      test("a running turn that is silent: its warnings, its inquiry and the row at the bottom", async ({ page }) => {
        await open(page, `#tasks/${A}/preview-run-a3`);
        await expect(rail(page).locator("[aria-current=step]")).toContainText(turn(1));
        const title = pane(page).locator("h3").first().locator("..");
        await expect(title).toContainText(running);
        await expect(title).toContainText(overdue);
        await expect(title).toContainText(stuck);
        const warning = title.getByText(stuck, { exact: true });
        expect(await warning.evaluate(node => getComputedStyle(node).color)).toBe(await page.evaluate(() => {
          const probe = document.createElement("i");
          probe.className = "text-hand";
          document.body.append(probe);
          const color = getComputedStyle(probe).color;
          probe.remove();
          return color;
        }));
        expect(await contrastOf(warning)).toBeGreaterThanOrEqual(4.5);
        await expect(pane(page).locator("[data-slot=detail-thread]")).toContainText(delivered);
        const live = pane(page).locator("[data-slot=detail-live]");
        await expect(live).toContainText(reported);
        await expect(live).toBeVisible();
        // The row stays at the bottom of the pane; the pane scrolls above it.
        const [liveBox, paneBox] = [(await live.boundingBox())!, (await pane(page).boundingBox())!];
        expect(Math.abs(liveBox.y + liveBox.height - (paneBox.y + paneBox.height))).toBeLessThan(2);
        await expectDetailWhole(page, { skipTitle: true });
      });

      test("a decision that waits: the request, and the dashed line that leads to it", async ({ page }) => {
        await open(page, `#tasks/${A}/preview-run-a2`);
        const selected = rail(page).locator("[aria-current=step]");
        await expect(selected).toContainText(waitingHost);
        await expect(pane(page).locator("h3").first()).toContainText(locale === "zh-CN" ? "协助请求" : "Assistance request");
        await expect(pane(page).getByText(waitingHost).first()).toBeVisible();
        const dashed = await rail(page).locator("li").filter({ hasText: waitingHost }).last().evaluate(node => getComputedStyle(node, "::before").borderLeftStyle);
        expect(dashed).toBe("dashed");
        await expectDetailWhole(page, { skipTitle: true });
        // A decision that was made says what, by whom and when.
        await step(page, locale === "zh-CN" ? "决定" : "Decision").first().click();
        await expect(pane(page)).toContainText(locale === "zh-CN" ? "拒绝" : "Declined");
        await expectDetailWhole(page, { skipTitle: true });
      });

      test("an accepted micro task: the artifact and the evidence of its integration", async ({ page }) => {
        await open(page, `#tasks/${A}/preview-run-a1/8`);
        await expect(pane(page).locator("h3").first()).toContainText(acceptance);
        await expect(pane(page)).toContainText(integration);
        await expect(pane(page)).toContainText("docs/reference/console.md");
        await expect(pane(page)).toContainText("refs/heads/synthetic-preview");
        await expectDetailWhole(page, { skipTitle: true });
      });

      test("a failed micro task: its error and the Host's conclusion, not counted as accepted", async ({ page }) => {
        await open(page, `#tasks/${B}/preview-run-b2`);
        await expect(detail(page)).toBeVisible();
        await expect(detail(page).locator("[data-slot=detail-header]")).toContainText(failed);
        await expect(pane(page)).toContainText("synthetic preview: harness quota exhausted before completion");
        await step(page, acceptance).click();
        const section = pane(page).getByRole("group", { name: conclusion });
        await expect(section).toContainText(notCounted);
        await expect(section).toContainText("art-b2-partial");
        await expectDetailWhole(page, { skipTitle: true });
      });

      test("only reads: no field to write in, and nothing but reads asked of the board", async ({ page }) => {
        const operations: string[] = [];
        page.on("request", request => {
          if (request.method() === "POST") operations.push(request.postDataJSON()?.operation);
        });
        await open(page, `#tasks/${A}/preview-run-a3`);
        await expect(detail(page)).toBeVisible();
        await expect(detail(page).locator("input, textarea, select, form")).toHaveCount(0);
        await expect(pane(page).locator("[data-slot=detail-live]")).toBeVisible();
        // The refresh reads again, with its time in the hint.
        await detail(page).getByRole("button", { name: refresh }).hover();
        await expect(page.locator("[data-slot=tooltip-content][data-state$=open]")).toContainText(/\d{2}:\d{2}:\d{2}/);
        await detail(page).getByRole("button", { name: refresh }).click();
        await expect.poll(() => operations.length).toBeGreaterThan(0);
        expect(new Set(operations.filter(Boolean))).toEqual(new Set(["workflow_get", "message_list"]));
      });
    });
  }

test.describe("the detail of a micro task in the dark theme", () => {
  test.use({ locale: "zh-CN", viewport: { width: 1280, height: 900 }, reducedMotion: "reduce" });
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
  });

  test("keeps its words legible", async ({ page }) => {
    await open(page, `#tasks/${A}/preview-run-a3`);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(detail(page)).toBeVisible();
    for (const text of await page.locator("[data-slot=task-detail] h2, [data-slot=task-detail] h3, [data-slot=detail-band] span, [data-slot=detail-live] span, [data-slot=detail-rail] span").all()) {
      if (!(await text.locator("*").count()) && (await text.innerText()).trim()) expect(await contrastOf(text), await text.innerText()).toBeGreaterThanOrEqual(4.5);
    }
  });
});
