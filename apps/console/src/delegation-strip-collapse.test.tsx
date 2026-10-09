import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { DelegationStrip } from "./ObjectiveOverview";
import { ObjectiveTimeline, type ObjectiveTimelineProps } from "./ObjectiveTimeline";
import { objectiveTimelineFixture } from "./objective-fixtures";

function manyCards(count = 21) {
  const timeline = objectiveTimelineFixture();
  const template = timeline.rows.find(row => row.parentRunId === null)!;
  timeline.rows = Array.from({ length: count }, (_, index) => ({ ...template, runId: `card-${index}`, rootRunId: `card-${index}`,
    title: `可辨认微任务 ${index + 1}`, createdAt: `2026-09-26T01:${String(index).padStart(2, "0")}:00Z` }));
  return timeline;
}
function setup(selectedRunId: string | null = null) {
  const timeline = manyCards();
  const onSelectRun = vi.fn();
  const onOpenRun = vi.fn();
  const onToggleCollapsed = vi.fn();
  const props = { timeline, selectedRunId, collapsed: false, onSelectRun, onOpenRun, onToggleCollapsed };
  const view = render(<DelegationStrip {...props} />);
  return { ...view, props, onSelectRun, onOpenRun, onToggleCollapsed, user: userEvent.setup() };
}
const cards = () => [...document.querySelectorAll<HTMLButtonElement>(".delegation-card")];
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("U7 reversible two-row delegation strip", () => {
  it("retains one two-state control and returns to precisely the first two rows", async () => {
    const view = setup();
    expect(cards()).toHaveLength(4); // default measured width 800: 2 cards per row
    const control = screen.getByRole("button", { name: "展开全部 21 个" });
    expect(control.getAttribute("aria-expanded")).toBe("false");
    const controlRow = control.parentElement;
    expect(controlRow?.className).toBe("delegation-band-head");
    expect(controlRow?.nextElementSibling?.classList.contains("delegation-strip")).toBe(true);
    await view.user.click(control);
    expect(cards()).toHaveLength(21);
    expect(screen.getByRole("button", { name: "收起" })).toBe(control);
    expect(control.parentElement).toBe(controlRow);
    expect(controlRow?.nextElementSibling?.classList.contains("delegation-strip")).toBe(true);
    expect(control.getAttribute("aria-expanded")).toBe("true");
    await view.user.click(control);
    expect(cards()).toHaveLength(4);
    expect(screen.getByRole("button", { name: "展开全部 21 个" })).toBe(control);
    expect(control.getAttribute("aria-expanded")).toBe("false");
  });

  it("identifies a selected hidden card after collapse and returns to it without losing selection", async () => {
    const view = setup("card-20");
    expect(cards()).toHaveLength(21);
    expect(cards()[20]!.getAttribute("aria-pressed")).toBe("true");
    await view.user.click(screen.getByRole("button", { name: "收起" }));
    expect(cards()).toHaveLength(4);
    expect(screen.queryByRole("button", { name: /已选中：第 21 个 · 可辨认微任务 21 · 返回卡片/ })).not.toBeNull();
    const locator = screen.getByRole("button", { name: /已选中：第 21 个 · 可辨认微任务 21 · 返回卡片/ });
    await view.user.click(locator);
    await waitFor(() => expect(document.activeElement).toBe(cards()[20]));
    expect(cards()[20]!.getAttribute("aria-pressed")).toBe("true");
    expect(view.onSelectRun).not.toHaveBeenCalled();
    expect(view.onOpenRun).not.toHaveBeenCalled();
  });

  it("reveals a new hidden selection while preserving a later manual collapse on refresh", async () => {
    const view = setup();
    view.rerender(<DelegationStrip {...view.props} selectedRunId="card-20" />);
    expect(cards()).toHaveLength(21);
    await view.user.click(screen.getByRole("button", { name: "收起" }));
    view.rerender(<DelegationStrip {...view.props} timeline={structuredClone(view.props.timeline)} selectedRunId="card-20" />);
    expect(cards()).toHaveLength(4);
    expect(screen.getByRole("button", { name: /已选中：第 21 个/ })).toBeTruthy();
  });

  it("keyboard entry reveals the hidden card and preserves a locator after collapse", async () => {
    const view = setup();
    act(() => cards()[0]!.focus());
    fireEvent.keyDown(cards()[0]!, { key: "End" });
    await waitFor(() => expect(document.activeElement).toBe(cards()[20]));
    expect(cards()).toHaveLength(21);
    expect(cards()[20]!.getAttribute("aria-label")).toContain("可辨认微任务 21");
    await view.user.click(screen.getByRole("button", { name: "收起" }));
    expect(cards()).toHaveLength(4);
    await view.user.click(screen.getByRole("button", { name: /键盘位置：第 21 个 · 可辨认微任务 21 · 返回卡片/ }));
    await waitFor(() => expect(document.activeElement).toBe(cards()[20]));
    expect(view.onSelectRun).not.toHaveBeenCalled();
  });

  it("advances from the last visible card into the hidden fifth card", async () => {
    setup();
    act(() => cards()[3]!.focus());
    fireEvent.keyDown(cards()[3]!, { key: "ArrowRight" });
    await waitFor(() => expect(document.activeElement).toBe(cards()[4]));
    expect(cards()).toHaveLength(21);
    expect(screen.getByRole("button", { name: "收起" }).getAttribute("aria-expanded")).toBe("true");
  });

  it("renders exactly two cards when responsive layout puts the strip into one column", async () => {
    const timeline = manyCards();
    render(<><style>{".delegation-strip { flex-direction: column; }"}</style>
      <DelegationStrip timeline={timeline} selectedRunId={null} collapsed={false}
        onSelectRun={() => {}} onOpenRun={() => {}} onToggleCollapsed={() => {}} />
    </>);
    expect(cards()).toHaveLength(2);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "展开全部 21 个" }));
    expect(cards()).toHaveLength(21);
    await user.click(screen.getByRole("button", { name: "收起" }));
    expect(cards()).toHaveLength(2);
  });

  it("survives a shorter refreshed range after keyboard focus was in its removed tail", async () => {
    const view = setup();
    fireEvent.keyDown(cards()[0]!, { key: "End" });
    await waitFor(() => expect(document.activeElement).toBe(cards()[20]));
    const shorter = { ...view.props.timeline, rows: view.props.timeline.rows.slice(0, 8) };
    view.rerender(<DelegationStrip {...view.props} timeline={shorter} />);
    expect(cards()).toHaveLength(8);
    // Fire before focusing the retained card so the old roving index still
    // exceeds the refreshed range; navigation must clamp it before stepping.
    fireEvent.keyDown(cards()[7]!, { key: "ArrowLeft" });
    expect(document.activeElement).toBe(cards()[6]);
    await view.user.click(screen.getByRole("button", { name: "收起" }));
    expect(cards()).toHaveLength(4);
    expect(screen.queryByRole("button", { name: /第 21 个/ })).toBeNull();
    expect(screen.getByRole("button", { name: /键盘位置：第 7 个/ })).toBeTruthy();
  });

  it("keeps the existing whole-band collapse independent of the two-row control and selection", async () => {
    const view = setup("card-20");
    await view.user.click(screen.getByRole("button", { name: "委派（21）" }));
    expect(view.onToggleCollapsed).toHaveBeenCalledOnce();
    view.rerender(<DelegationStrip {...view.props} collapsed />);
    expect(cards()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "委派（21）" }).getAttribute("aria-expanded")).toBe("false");
    view.rerender(<DelegationStrip {...view.props} />);
    expect(cards()[20]!.getAttribute("aria-pressed")).toBe("true");
  });
});

describe("U7 measured card budget and independent scrolling", () => {
  let style: HTMLStyleElement;
  let originalScroll: PropertyDescriptor | undefined;
  beforeEach(() => {
    originalScroll = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
    style = document.createElement("style");
    style.textContent = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    // jsdom cannot resolve the var(--line) border shorthand. Supply its
    // existing 1px geometry explicitly as part of the measured fixture.
    style.textContent += "\n.delegation-band { border-bottom: 1px solid black; }";
    document.head.append(style);
  });
  afterEach(() => {
    style.remove();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", originalScroll ?? { configurable: true, writable: true, value: undefined });
  });

  // These are supplied measurements, not jsdom layout. Check the production
  // observer's budget and the real CSS/DOM contract; Host checks pixels/scroll.
  function measuredColumn(singleColumn = false) {
    const sizes = { column: 462, header: 145, toolbar: 40, drawer: 44, controls: 32, locator: 32, banner: 0, separator: 0 };
    const observers: { callback: ResizeObserverCallback; targets: Element[] }[] = [];
    vi.stubGlobal("ResizeObserver", class {
      targets: Element[] = [];
      constructor(public callback: ResizeObserverCallback) { observers.push(this); }
      observe(target: Element) { this.targets.push(target); }
      unobserve() {}
      disconnect() {}
    });
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function (this: HTMLElement) {
      return this.matches(".timeline-view") ? sizes.column : 0;
    });
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
      return this.matches(".delegation-strip") ? 744 : 0;
    });
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (this: HTMLElement) {
      if (this.matches(".tl-head")) return sizes.header;
      if (this.matches(".tl-toolbar")) return sizes.toolbar;
      if (this.matches(".inspector-dock")) return sizes.drawer;
      if (this.matches(".inspector-separator")) return sizes.separator;
      if (this.matches(".trunc-banner")) return sizes.banner;
      if (this.matches(".delegation-band-head")) return sizes.controls;
      if (this.matches(".delegation-current")) return sizes.locator;
      return 0;
    });
    const timeline = manyCards(24);
    const props: ObjectiveTimelineProps = {
      summary: timeline.objective, timeline, loading: false, error: "", stale: false,
      newRunIds: new Set(), hidden: false, openedKey: null, openedRunId: null, selection: null,
      onSelectItem: vi.fn(), onOpenItem: vi.fn(), onSelectRun: vi.fn(), onOpenRun: vi.fn(),
      onClearSelection: vi.fn(), onRetry: vi.fn(), onBackToList: vi.fn(),
    };
    if (singleColumn) style.textContent += "\n.delegation-strip { flex-direction: column; }";
    const view = render(<ObjectiveTimeline {...props} />);
    const root = view.container.querySelector<HTMLElement>(".timeline-view")!;
    const band = root.querySelector<HTMLElement>(".delegation-band")!;
    const strip = band.querySelector<HTMLElement>(".delegation-strip")!;
    const resize = () => act(() => {
      for (const observer of observers) observer.callback([], observer as unknown as ResizeObserver);
    });
    return { ...view, props, sizes, root, band, strip, observers, resize, user: userEvent.setup() };
  }

  it.each([false, true])("budgets a 24-card expanded strip from available panel height (one column: %s)", async singleColumn => {
    const view = measuredColumn(singleColumn);
    const control = screen.getByRole("button", { name: "展开全部 24 个" });
    await view.user.click(control);
    expect(cards()).toHaveLength(24);
    expect(view.band.style.maxHeight).toBe("113px"); // 462 − 145 − 40 − 44 − 120
    expect(view.band.style.minHeight).toBe("81px"); // 32px controls + 48px scrollport + border
    const bandCSS = getComputedStyle(view.band);
    const stripCSS = getComputedStyle(view.strip);
    expect(bandCSS.display).toBe("flex");
    expect(bandCSS.flexDirection).toBe("column");
    expect(Number(bandCSS.flexShrink)).toBeGreaterThan(0);
    expect(stripCSS.overflowY).toBe("auto");
    expect(stripCSS.overflowX).toBe("hidden");
    expect(stripCSS.minHeight).toBe("48px");
    expect(stripCSS.flexWrap).toBe(singleColumn ? "nowrap" : "wrap");
    expect(Number(stripCSS.flexShrink)).toBeGreaterThan(0);
    expect(getComputedStyle(cards()[23]!).flexShrink).toBe("0");
    expect(view.strip.contains(control)).toBe(false);
    expect(getComputedStyle(control.parentElement!).flexShrink).toBe("0");
    expect(getComputedStyle(view.root.querySelector(".timeline-body, .tl-body")!).minHeight).toBe("120px");
    expect(getComputedStyle(view.root.querySelector(".tl-toolbar")!).flexShrink).toBe("0");
    expect(getComputedStyle(view.root).overflowY).toBe("auto");
    const observed = view.observers.flatMap(observer => observer.targets);
    expect(observed).toContain(view.root.querySelector(".tl-head"));
    expect(observed).toContain(view.root.querySelector(".tl-toolbar"));
    expect(observed).toContain(view.root.querySelector(".inspector-dock"));
    expect(observed).toContain(control.parentElement);
    // A newly mounted warning must enter the budget and observer target set.
    view.sizes.banner = 20;
    view.rerender(<ObjectiveTimeline {...view.props} timeline={{ ...view.props.timeline!, filtered: true }} />);
    expect(view.band.style.maxHeight).toBe("85px"); // 113 − 20px banner − its existing 8px margin
    expect(view.observers.flatMap(observer => observer.targets)).toContain(view.root.querySelector(".trunc-banner"));
    view.rerender(<ObjectiveTimeline {...view.props} />);
    expect(view.band.style.maxHeight).toBe("113px");
    view.sizes.column = 700;
    view.sizes.drawer = 168;
    view.sizes.separator = 10;
    await view.user.click(screen.getByRole("button", { name: "展开检查器" }));
    view.resize();
    expect(view.band.style.maxHeight).toBe("217px"); // 700 − 145 − 40 − 168 − 10 − 120
    expect(view.observers.flatMap(observer => observer.targets)).toContain(view.root.querySelector(".inspector-separator"));
    await view.user.click(screen.getByRole("button", { name: "收起检查器" }));
    view.sizes.column = 462;
    view.sizes.drawer = 44;
    view.sizes.separator = 0;
    view.resize();
    expect(view.band.style.maxHeight).toBe("113px");
    // Wrapped heading: measured fixed sections grow, so the band yields.
    view.sizes.header = 175;
    view.resize();
    expect(view.band.style.maxHeight).toBe("83px");
    view.sizes.column = 400;
    view.resize();
    expect(view.band.style.maxHeight).toBe("81px"); // usable floor; root may scroll
    view.sizes.column = 900;
    view.resize();
    expect(view.band.style.maxHeight).toBe("360px"); // never above 40% of the panel
    view.sizes.column = 0;
    view.resize();
    expect(view.band.style.maxHeight).toBe("360px"); // hidden columns retain valid geometry
    view.sizes.column = 900;
    view.strip.scrollTop = 2100;
    await view.user.click(control);
    expect(view.strip.scrollTop).toBe(0);
    expect(cards()).toHaveLength(singleColumn ? 2 : 4);
    expect(control.getAttribute("aria-expanded")).toBe("false");
    await view.user.click(screen.getByRole("button", { name: "委派（24）" }));
    expect(cards()).toHaveLength(0);
    expect(view.band.style.minHeight).toBe("");
    expect(view.band.style.maxHeight).toBe("");
    expect(getComputedStyle(view.band).maxHeight).toBe("none");
    await view.user.click(screen.getByRole("button", { name: "委派（24）" }));
    expect(cards()).toHaveLength(singleColumn ? 2 : 4);
    expect(view.band.style.maxHeight).toBe("360px");
  });

  it("keeps hidden selection/keyboard locators outside the card scrollport and scrolls back to their card", async () => {
    const view = measuredColumn(true);
    const scrollCard = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollCard });
    const previousFocus = document.activeElement;
    view.rerender(<ObjectiveTimeline {...view.props} selection={{ type: "run", runId: "card-23" }} />);
    await waitFor(() => expect(scrollCard.mock.contexts).toContain(cards()[23]));
    expect(document.activeElement).toBe(previousFocus);
    expect(cards()[23]!.getAttribute("aria-pressed")).toBe("true");
    scrollCard.mockClear();
    fireEvent.keyDown(cards()[0]!, { key: "End" });
    await waitFor(() => expect(document.activeElement).toBe(cards()[23]));
    expect(scrollCard.mock.contexts).toContain(cards()[23]);
    await view.user.click(screen.getByRole("button", { name: "收起" }));
    const locator = screen.getByRole("button", { name: /已选中：第 24 个/ });
    expect(view.strip.contains(locator)).toBe(false);
    expect(locator.parentElement?.className).toBe("delegation-current");
    expect(getComputedStyle(locator.parentElement!).flexShrink).toBe("0");
    expect(view.band.style.minHeight).toBe("113px");
    await view.user.click(locator);
    await waitFor(() => expect(document.activeElement).toBe(cards()[23]));
    expect(scrollCard).toHaveBeenLastCalledWith({ block: "nearest", inline: "nearest" });
    expect(scrollCard.mock.contexts.at(-1)).toBe(cards()[23]);
    expect(cards()[23]!.getAttribute("aria-pressed")).toBe("true");
  });
});
