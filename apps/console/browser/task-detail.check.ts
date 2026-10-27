import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { contrastOf, expectNoSidewaysScroll, expectTextUncut, expectWholeAndApart, open, settle, test } from "./preview";

// The detail of a micro task on the synthetic preview. Macro task A holds a1 (accepted, two turns, a change
// of buddy, an answered inquiry and one that could not be delivered), a2 (waiting for its Host), a3 (running,
// silent for 19 minutes, with an inquiry delivered and not answered). Macro task B holds b2 (failed, concluded).
const A = "macro-11111111-1111-4111-8111-111111111111";
const B = "macro-22222222-2222-4222-8222-222222222222";

const languages = [
  {
    locale: "zh-CN", refresh: "重新读取", turn: (n: number) => `回合 ${n}`, delegate: "委派", acceptance: "验收",
    running: "进行中", stuck: "可能卡住", overdue: "已超过预期", waitingHost: "等待 Host", failed: "失败", mode: "快速",
    answered: "已答复", unavailable: "问询已结束", delivered: "已送达，等待答复", conclusion: "Host 的结论", notCounted: "不计入验收",
    integration: "整合", reported: /上报 \d+ 分钟前/, handoff: "换手", unsure: "结束未确认", helperDone: "已完成", ask: "上一轮停下了吗？",
  },
  {
    locale: "en-US", refresh: "Read again", turn: (n: number) => `Turn ${n}`, delegate: "Delegate", acceptance: "Acceptance",
    running: "Running", stuck: "Possibly stuck", overdue: "Overdue", waitingHost: "Waiting for Host", failed: "Failed", mode: "Fast",
    answered: "Answered", unavailable: "Unavailable", delivered: "Delivered, awaiting answer", conclusion: "Host's conclusion", notCounted: "not counted as accepted",
    integration: "Integration", reported: /Reported \d+ min ago/, handoff: "Handoff", unsure: "Stop not confirmed", helperDone: "Done", ask: "上一轮停下了吗？",
  },
];

const detail = (page: Page) => page.locator("[data-slot=task-detail]");
/** The steps along the top: the micro task's own row of the macro task, one selected slice at a time. */
const strip = (page: Page) => page.locator("[data-slot=detail-strip]");
const chosen = (page: Page) => strip(page).locator("[aria-pressed=true]");
const pane = (page: Page) => page.locator("[data-slot=detail-pane]");
/** The scrolled content of the pane: the frame its parts have to stay inside, however far it scrolls. */
const content = (page: Page) => pane(page).locator("div.w-0.min-w-full").first();

/** Read the drawn selection frame, excluding the larger invisible SVG hit and focus areas. */
async function stripPosition(page: Page) {
  return strip(page).locator('[data-slot=detail-strip-scroll]').evaluate(view => {
    const frame = view.getBoundingClientRect();
    const selection = view.querySelector<SVGGraphicsElement>('[data-slot=selected-item]')!;
    const slice = selection.getBoundingClientRect(), canvas = selection.ownerSVGElement!.getBoundingClientRect();
    const drawnLeft = Math.max(slice.left, canvas.left), drawnRight = Math.min(slice.right, canvas.right);
    const left = frame.left + view.clientLeft;
    return { left: drawnLeft - left, right: drawnRight - left, width: drawnRight - drawnLeft,
      available: view.clientWidth, scrollLeft: view.scrollLeft, top: frame.top, pageTop: window.scrollY };
  });
}

async function expectSelectedInView(page: Page) {
  await expect.poll(async () => {
    const slice = await stripPosition(page);
    return slice.left >= -1 && (slice.width > slice.available ? Math.abs(slice.left) <= 1 : slice.right <= slice.available + 1);
  }, { message: "the selected slice is whole, or its beginning is visible when wider than the strip" }).toBe(true);
}

/** Every part of the open step is whole, apart from its neighbours, and drawn in full. */
async function expectDetailWhole(page: Page, { skipTitle = false } = {}) {
  const header = detail(page).locator("[data-slot=detail-header]");
  const line = header.locator(":scope > div:first-child");
  await expectWholeAndApart(header, line.locator(":scope > *"));
  // The title is cut with an ellipsis by design; everything else in the head is written whole.
  await expectTextUncut(line.locator("button, dt, dd, span:not([data-slot=state-mark])").filter({ hasNot: page.locator("h2") }));
  const canvas = strip(page).locator('[data-slot=detail-strip-scroll] > div > div');
  // Events and inquiries share the time scale with bars; the axis and row occupy separate bands.
  await expectWholeAndApart(canvas, strip(page).locator('[data-slot=timeline-axis], [data-slot=timeline-track]'));
  await expectTextUncut(strip(page).locator("text:visible"));
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
    header.locator("button").first().boundingBox(),
  ]);
  const centre = (box: { y: number; height: number }) => box.y + box.height / 2;
  expect(Math.abs(centre(status!) - centre(again!)), "the state stays beside refresh").toBeLessThan(10);
  expect(Math.abs(centre(mark!) - centre(title!)), "the back button stays beside the title").toBeLessThan(10);
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

/** A slice of the strip by the words its readout carries. */
const step = (page: Page, text: string | RegExp) => strip(page).getByRole("button", { name: typeof text === "string"
  ? new RegExp(`^${text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?: ·|$)`) : text });

for (const { locale } of languages) for (const theme of ["dark", "light"]) for (const reducedMotion of ["reduce", "no-preference"] as const) {
  test.describe(`strip selection ${locale} ${theme} ${reducedMotion}`, () => {
    test.use({ locale, viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });

    test("keeps the selected slice in view through arrows, navigation buttons and clicks after zooming", async ({ page }, testInfo) => {
      await page.addInitScript(value => localStorage.setItem("hey-my-buddy.console.theme", value), theme);
      await open(page, `#tasks/${A}/preview-run-a1/1`);
      await expect(chosen(page)).toHaveAttribute("data-key", "delegation");
      const ruler = strip(page).locator('[data-slot=zoom-ruler]:visible');
      const track = strip(page).locator('[data-slot=timeline-track]');
      const fitted = Number(await track.getAttribute("width"));
      for (let index = 0; index < 3; index++) await ruler.press("+");
      await expect.poll(async () => Number(await track.getAttribute("width"))).toBeGreaterThan(fitted * 3);
      // Finish zooming at a settled scale, then exercise the user's scroll motion preference.
      await page.emulateMedia({ reducedMotion });
      await expectSelectedInView(page);
      const start = await stripPosition(page);
      const total = Number((await strip(page).locator('[data-slot=step-counter]').innerText()).match(/\/\s*(\d+)$/)![1]);
      const select = async (action: () => Promise<unknown>, number: number) => {
        await action();
        await expect(page).toHaveURL(new RegExp(`/preview-run-a1/${number}$`));
        try { await expectSelectedInView(page); }
        catch (error) {
          await page.screenshot({ path: testInfo.outputPath(`offscreen-step-${number}.png`) });
          throw error;
        }
        const position = await stripPosition(page);
        expect(position.top, "the strip never moves vertically").toBe(start.top);
        expect(position.pageTop, "the page never scrolls vertically").toBe(start.pageTop);
      };
      await page.screenshot({ path: testInfo.outputPath("strip-start.png") });
      for (let number = 2; number <= total; number++) {
        await select(() => page.keyboard.press("ArrowRight"), number);
        if (number === 2) expect((await stripPosition(page)).scrollLeft, "an already visible slice leaves the scroll alone").toBe(start.scrollLeft);
      }
      expect((await stripPosition(page)).scrollLeft).toBeGreaterThan(start.scrollLeft + start.available);
      await page.screenshot({ path: testInfo.outputPath("strip-right.png") });
      for (let number = total - 1; number >= 1; number--) await select(() => page.keyboard.press("ArrowLeft"), number);
      await page.screenshot({ path: testInfo.outputPath("strip-left.png") });

      const next = strip(page).getByRole("button", { name: locale === "zh-CN" ? "下一步" : "Next step", exact: true });
      const previous = strip(page).getByRole("button", { name: locale === "zh-CN" ? "上一步" : "Previous step", exact: true });
      for (let number = 2; number <= total; number++) await select(() => next.click(), number);
      for (let number = total - 1; number >= 1; number--) await select(() => previous.click(), number);

      // Click the visible portion of the oversized first turn without Playwright scrolling it into view first.
      const target = strip(page).locator('[data-key="span:execution:att-a1-1"] [data-slot=execution-bar]');
      const view = (await strip(page).locator('[data-slot=detail-strip-scroll]').boundingBox())!;
      const bar = (await target.boundingBox())!;
      const x = (Math.max(view.x, bar.x) + Math.min(view.x + view.width, bar.x + bar.width)) / 2;
      await select(() => page.mouse.click(x, bar.y + bar.height / 2), 3);
      expect((await stripPosition(page)).width).toBeGreaterThan(start.available);
    });
  });
}

for (const { locale, refresh, turn, delegate, acceptance, running, stuck, overdue, waitingHost, failed, mode, answered, unavailable, delivered, conclusion, notCounted, integration, reported, handoff, unsure, helperDone, ask } of languages)
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
        await expect(chosen(page)).toHaveAccessibleName(new RegExp(turn(2)));
        // The macro task's own header stays; the lanes give way to the detail, and the list beside it holds the rows.
        await expect(page.locator("[data-slot=macro-overview-header] h2")).toBeVisible();
        if (width > 760) await expect(page.locator('[data-slot=task-names] [data-key="row:preview-run-a1"]')).toBeVisible();
        else await expect(page.locator('[data-slot=task-names]')).toBeHidden();
        await expect(page.locator('[data-slot=timeline-tracks]')).toHaveCount(0);
        await page.goBack();
        await expect(page).toHaveURL(new RegExp(`#tasks/${A}$`));
        await expect(detail(page)).toHaveCount(0);
        await page.goForward();
        await expect(page).toHaveURL(new RegExp(`#tasks/${A}/preview-run-a1/7$`));
        await expect(chosen(page)).toHaveAccessibleName(new RegExp(turn(2)));
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
        await expect(pane(page)).toContainText(locale === "zh-CN" ? "批准" : "Approved");
        // No step in the address: the detail settles on the last turn and writes it into the address.
        await go(page, `#tasks/${A}/preview-run-a1`);
        await expect(chosen(page)).toHaveAccessibleName(new RegExp(turn(2)));
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

      test("hangs the steps along the top while the pane below scrolls on its own", async ({ page }) => {
        await open(page, `#tasks/${A}/preview-run-a1/7`);
        await expect(detail(page)).toBeVisible();
        await expect(strip(page)).toBeVisible();
        const [headerBox, stripBox, paneBox, namesBox] = [
          (await detail(page).locator("[data-slot=detail-header]").boundingBox())!,
          (await strip(page).boundingBox())!,
          (await pane(page).boundingBox())!,
          (await page.locator('[data-slot=task-names]').boundingBox())!,
        ];
        expect(stripBox.y).toBeGreaterThanOrEqual(headerBox.y + headerBox.height - 1);
        expect(paneBox.y).toBeGreaterThanOrEqual(stripBox.y + stripBox.height - 1);
        if (width > 760) {
          expect(paneBox.x).toBeGreaterThanOrEqual(namesBox.x + namesBox.width - 1);
        } else {
          await expect(page.locator('[data-slot=task-names]')).toBeHidden();
        }
        await expectDetailWhole(page, { skipTitle: true });
        await expectOneLineHead(page, refresh, width <= 760);
        // A step reached by the keyboard is the one shown.
        await step(page, delegate).focus();
        await page.keyboard.press("Enter");
        await expect(page).toHaveURL(new RegExp(`#tasks/${A}/preview-run-a1/1$`));
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
        await expect(band).toContainText("dsh / deepseek-official / deepseek-flash / max");
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

      test("a stop that is not confirmed is written as not confirmed, in the turn and on the strip", async ({ page }) => {
        await open(page, `#tasks/${A}/preview-run-a1-h1-h1`);
        await expect(detail(page)).toBeVisible();
        const title = pane(page).locator("h3").first().locator("..");
        await expect(title).toContainText(unsure);
        await expect(title).not.toContainText(locale === "zh-CN" ? "已结束" : "Ended");
        await expect(chosen(page)).toHaveAccessibleName(new RegExp(locale === "zh-CN" ? "停止未确认" : "Stop unconfirmed"));
        const mark = await chosen(page).locator("[data-slot=execution-status-outline]").evaluate(node => node.getAttribute("stroke-dasharray"));
        expect(mark).toBe("3 2");
        await expectDetailWhole(page, { skipTitle: true });
      });

      test("a running turn that is silent: its warnings, its inquiry and the row at the bottom", async ({ page }) => {
        await open(page, `#tasks/${A}/preview-run-a3`);
        await expect(chosen(page)).toHaveAccessibleName(new RegExp(turn(1)));
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
        const selected = chosen(page);
        await expect(selected).toHaveAccessibleName(new RegExp(waitingHost));
        await expect(pane(page).locator("h3").first()).toContainText(locale === "zh-CN" ? "协助请求" : "Assistance request");
        await expect(pane(page).getByText(waitingHost).first()).toBeVisible();
        const dashed = await strip(page).locator("[data-slot=host-wait]").last().evaluate(node => node.getAttribute("stroke-dasharray"));
        expect(dashed).toBe("5 4");
        await expectDetailWhole(page, { skipTitle: true });
        // A decision that was made says what, by whom and when.
        // Enlarge the shared scale until the decision and its nearby continuation are separate marks.
        for (let count = 0; count < 5 && await step(page, locale === "zh-CN" ? "决定" : "Decision").count() === 0; count++) {
          await strip(page).locator('[data-slot=zoom-ruler]:visible').press("+");
        }
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
        await step(page, locale === "zh-CN" ? "结论" : "Conclusion").click();
        const section = pane(page).getByRole("group", { name: conclusion });
        await expect(section).toContainText(notCounted);
        await expect(section).toContainText("art-b2-partial");
        await expectDetailWhole(page, { skipTitle: true });
      });

      test("a quota rejection names the earlier buddy and the reroute, while the completed task succeeds", async ({ page }, testInfo) => {
        await open(page, `#tasks/${B}/preview-run-b9`);
        await expect(detail(page).locator("[data-slot=detail-status]")).toHaveText(locale === "zh-CN" ? "已验收" : "Accepted");
        await step(page, turn(1)).click();
        await expect(pane(page)).toContainText(locale === "zh-CN" ? "模型开始前被拒绝" : "Rejected before the model started");
        await expect(pane(page)).toContainText(locale === "zh-CN" ? "已改派" : "Rerouted");
        await expect(pane(page)).toContainText("zcode");
        await page.screenshot({ path: testInfo.outputPath(`task-rerouted-${locale}-${width}-light.png`) });
        await expect(step(page, locale === "zh-CN" ? "路由" : "Routing")).toHaveCount(1);
        await step(page, locale === "zh-CN" ? "路由" : "Routing").last().click();
        await expect(chosen(page)).toHaveAccessibleName(new RegExp(locale === "zh-CN" ? "路由" : "Routing"));
        await expectDetailWhole(page, { skipTitle: true });
      });

      test("a route with every candidate cooling shows the next retry and the frozen buddies", async ({ page }, testInfo) => {
        await open(page, `#tasks/${A}/preview-run-a4`);
        await expect(pane(page)).toContainText(locale === "zh-CN" ? "所有候选都在冷却" : "All candidates are cooling down");
        await pane(page).getByRole("button", { name: locale === "zh-CN" ? "提交时的约束" : "Bounds at submission" }).click();
        await expect(pane(page)).toContainText(locale === "zh-CN" ? "冷却中的候选 2 个" : "Cooling down: 2");
        await expect(pane(page)).toContainText("dsh");
        await page.screenshot({ path: testInfo.outputPath(`task-cooling-${locale}-${width}-light.png`) });
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
    await expect(strip(page)).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await settle(page);
    const texts = page.locator("[data-slot=task-detail] h2, [data-slot=task-detail] h3, [data-slot=detail-band] span, [data-slot=detail-live] span, [data-slot=detail-strip] text");
    const count = await texts.count();
    expect(count, "detail text to check").toBeGreaterThan(0);
    for (let index = 0; index < count; index++) {
      const text = texts.nth(index);
      const { label, hasChildren } = await text.evaluate(node => ({
        label: node.textContent?.trim(), hasChildren: node.childElementCount > 0,
      }));
      if (!hasChildren && label) expect(await contrastOf(text), label).toBeGreaterThanOrEqual(4.5);
    }
  });
});

for (const locale of ["zh-CN", "en-US"]) for (const width of [390, 1280]) {
  test.describe(`model service overload ${locale} ${width}px`, () => {
    test.use({ locale, viewport: { width, height: 900 }, reducedMotion: "reduce" });
    test("shows the structured overload on its micro task", async ({ page }) => {
      await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
      await page.route("**/api/tasks/preview-run-b2", async route => {
        const response = await route.fetch();
        const task = await response.json();
        task.providerRejection = { code: "overloaded", nativeCode: "serverOverloaded", rejectionPhase: "during-model" };
        await route.fulfill({ response, json: task });
      });
      await page.route("**/api/command", async route => {
        const request = route.request().postDataJSON();
        if (request.operation !== "workflow_get" || request.params.runId !== "preview-run-b9") return route.continue();
        const response = await route.fetch();
        const reply = await response.json();
        for (const turn of [...(reply.result.turns ?? []), ...(reply.result.audit?.turns ?? [])]) {
          if (turn.state === "rejected") turn.providerRejection = { code: "overloaded", nativeCode: "serverOverloaded" };
        }
        await route.fulfill({ response, json: reply });
      });
      await open(page, "#tasks/macro-22222222-2222-4222-8222-222222222222/preview-run-b2");
      const label = locale === "zh-CN" ? "模型服务满载" : "Model service overloaded";
      await expect(pane(page)).toContainText(label);
      await expectNoSidewaysScroll(page);
      await expectTextUncut(pane(page).getByText(label, { exact: false }));
      await page.evaluate(() => { window.location.hash = "#tasks/macro-22222222-2222-4222-8222-222222222222/preview-run-b9"; });
      await page.locator("[data-slot=detail-strip] [data-key='span:execution:att-b9-1']").click();
      await expect(pane(page)).toContainText(label);
      await expect(pane(page)).toContainText(locale === "zh-CN" ? "已改派" : "Rerouted");
      await expectNoSidewaysScroll(page);
    });
  });
}
