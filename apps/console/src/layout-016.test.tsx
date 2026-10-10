import { useRef, useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ObjectiveTimeline } from "./ObjectiveTimeline";
import type { ObjectiveTimelineProps } from "./ObjectiveTimeline";
import { ObjectiveList } from "./ObjectiveList";
import { Popover } from "./Popover";
import { listFixture, objectiveTimelineFixture } from "./objective-fixtures";
import type { ObjectiveTimeline as ObjectiveTimelineData } from "./objective-types";
import type { InspectorSelection } from "./inspector-card";

/** Host UI review of the 0.16 first build (layout correctness, §1–§11). */

const restorers: (() => void)[] = [];
afterEach(() => {
  cleanup();
  while (restorers.length) restorers.pop()!();
  vi.unstubAllGlobals();
});

/** Override a DOM getter on a prototype for one test, restoring it afterwards. */
function stubGetter<T extends object>(proto: T, name: string, get: (this: HTMLElement) => unknown) {
  const own = Object.getOwnPropertyDescriptor(proto, name);
  Object.defineProperty(proto, name, { configurable: true, get });
  restorers.push(() => {
    if (own) Object.defineProperty(proto, name, own);
    else delete (proto as Record<string, unknown>)[name];
  });
}

function stubWindow(name: "innerWidth" | "innerHeight", value: number) {
  const own = Object.getOwnPropertyDescriptor(window, name);
  Object.defineProperty(window, name, { configurable: true, writable: true, value });
  restorers.push(() => { if (own) Object.defineProperty(window, name, own); });
}

function baseProps(timeline: ObjectiveTimelineData | null, overrides: Record<string, unknown> = {}): ObjectiveTimelineProps {
  const props: ObjectiveTimelineProps = {
    summary: timeline?.objective ?? null, timeline, loading: false, error: "", stale: false,
    newRunIds: new Set<string>(), hidden: false, openedKey: null, openedRunId: null, selection: null,
    onSelectItem: vi.fn(), onOpenItem: vi.fn(), onSelectRun: vi.fn(), onOpenRun: vi.fn(),
    onClearSelection: vi.fn(), onRetry: vi.fn(), onBackToList: vi.fn(),
  };
  return { ...props, ...overrides } as ObjectiveTimelineProps;
}

const fitButton = () => screen.getByRole("button", { name: "适应窗口" });
const span = (key: string) => document.querySelector<HTMLElement>(`[data-key="${key}"]`)!;

describe("§3 keyboard zoom keeps browser shortcuts", () => {
  it("ignores Ctrl/Meta/Alt with +, -, = and 0; Shift+= / + still zooms", () => {
    render(<ObjectiveTimeline {...baseProps(objectiveTimelineFixture())} />);
    const target = span("span:s-r1-e");
    for (const modifier of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }]) {
      for (const key of ["+", "=", "-", "0"]) {
        // Not prevented: the browser keeps its own zoom.
        expect(fireEvent.keyDown(target, { key, ...modifier })).toBe(true);
        expect(fitButton().getAttribute("aria-pressed")).toBe("true");
      }
    }
    expect(fireEvent.keyDown(target, { key: "+", shiftKey: true })).toBe(false);
    expect(fitButton().getAttribute("aria-pressed")).toBe("false");
    fireEvent.keyDown(target, { key: "0" });
    expect(fitButton().getAttribute("aria-pressed")).toBe("true");
  });
});

describe("§1/§2/§4 fit width and the zoom anchor", () => {
  /** A 900px scroll viewport with the 240px label fallback: a 660px canvas. */
  function stubCanvas() {
    stubGetter(HTMLElement.prototype, "clientWidth", () => 900);
    const scroll = new Map<Element, number>();
    const own = Object.getOwnPropertyDescriptor(Element.prototype, "scrollLeft");
    Object.defineProperty(Element.prototype, "scrollLeft", {
      configurable: true,
      get(this: Element) { return scroll.get(this) ?? 0; },
      set(this: Element, value: number) { scroll.set(this, value); },
    });
    restorers.push(() => { if (own) Object.defineProperty(Element.prototype, "scrollLeft", own); });
  }
  const canvasPx = () => Number(/\+ ([\d.]+)px/.exec((document.querySelector(".tl-grid") as HTMLElement).style.width)![1]);
  const trackPx = () => canvasPx() - 12;
  const scroller = () => document.querySelector(".tl-scroll") as HTMLElement;
  const screenX = (key: string) => 240 + Number.parseFloat(span(key).style.left) / 100 * trackPx() - scroller().scrollLeft;

  it("fits the canvas to the actual viewport with no internal overflow", () => {
    stubCanvas();
    render(<ObjectiveTimeline {...baseProps(objectiveTimelineFixture())} />);
    // 900 − 240 = 660: exactly the viewport, never a 380px floor or a
    // band-budget minimum that would force horizontal scrolling.
    expect(canvasPx()).toBe(660);
    const bands = [...document.querySelectorAll<HTMLElement>(".fold-band")].map(band => Number.parseFloat(band.style.width));
    expect(bands.length).toBeGreaterThan(0);
    for (const band of bands) expect(band).toBe(32);
    expect(bands.reduce((sum, band) => sum + band, 0)).toBeLessThanOrEqual(trackPx() * 0.4 + 1e-6);
  });

  function zoomedScroll(selection: InspectorSelection | null, targetScreenX: number) {
    const view = render(<ObjectiveTimeline {...baseProps(objectiveTimelineFixture(), { selection })} />);
    fireEvent.keyDown(span("span:s-r1-e"), { key: "+" });
    fireEvent.keyDown(span("span:s-r1-e"), { key: "+" });
    // Scroll so the selected span's start sits at targetScreenX.
    const percent = Number.parseFloat(span("span:s-r2-e2").style.left);
    scroller().scrollLeft = 240 + percent / 100 * trackPx() - targetScreenX;
    fireEvent.keyDown(span("span:s-r1-e"), { key: "+" });
    const result = { scrollLeft: scroller().scrollLeft, selectedX: screenX("span:s-r2-e2") };
    view.unmount();
    return result;
  }

  it("anchors on a selection only when it is right of the sticky labels, else on the visible track centre", () => {
    stubCanvas();
    const selection: InspectorSelection = { type: "item", key: "span:s-r2-e2" };
    // Visible selection: it keeps its screen position.
    const visible = zoomedScroll(selection, 500);
    expect(Math.abs(visible.selectedX - 500)).toBeLessThan(1);
    // Under the labels (screen x 140 < 240): treated exactly like no
    // selection — the visible track centre is the anchor.
    const hidden = zoomedScroll(selection, 140);
    const none = zoomedScroll(null, 140);
    expect(hidden.scrollLeft).toBe(none.scrollLeft);
    expect(hidden.selectedX).not.toBeCloseTo(140, 0);
  });
});

describe("§5 zoom follows the objective", () => {
  it("keeps a manual zoom through polling and a detail return, and resets to fit on an objective switch", () => {
    const first = objectiveTimelineFixture();
    const second = objectiveTimelineFixture({ objective: { ...first.objective, objectiveId: "obj-2" } });
    const view = render(<ObjectiveTimeline {...baseProps(first)} />);
    fireEvent.keyDown(span("span:s-r1-e"), { key: "+" });
    expect(fitButton().getAttribute("aria-pressed")).toBe("false");
    // Same-objective refresh (new object, later observation).
    view.rerender(<ObjectiveTimeline {...baseProps({ ...objectiveTimelineFixture(), observedAt: "2026-09-26T08:13:00Z" })} />);
    expect(fitButton().getAttribute("aria-pressed")).toBe("false");
    // Detail open and return: the view hides and shows again.
    view.rerender(<ObjectiveTimeline {...baseProps(objectiveTimelineFixture(), { hidden: true })} />);
    view.rerender(<ObjectiveTimeline {...baseProps(objectiveTimelineFixture())} />);
    expect(fitButton().getAttribute("aria-pressed")).toBe("false");
    // An actual switch starts from 适应窗口, even when switching back.
    view.rerender(<ObjectiveTimeline {...baseProps(second)} />);
    expect(fitButton().getAttribute("aria-pressed")).toBe("true");
    view.rerender(<ObjectiveTimeline {...baseProps(objectiveTimelineFixture())} />);
    expect(fitButton().getAttribute("aria-pressed")).toBe("true");
  });
});

describe("§6 strict stop evidence for the acceptance-wait line", () => {
  it("draws nothing when the finished, ended execution has a null or false shutdown", () => {
    for (const shutdownConfirmed of [null, false]) {
      const timeline = objectiveTimelineFixture();
      const exec = timeline.spans.find(entry => entry.spanId === "s-r1-e")!;
      (exec as { shutdownConfirmed: boolean | null }).shutdownConfirmed = shutdownConfirmed;
      const view = render(<ObjectiveTimeline {...baseProps(timeline)} />);
      expect(span("span:s-r1-e").parentElement!.querySelector(".accept-wait-line")).toBeNull();
      view.unmount();
    }
    const confirmed = render(<ObjectiveTimeline {...baseProps(objectiveTimelineFixture())} />);
    expect(span("span:s-r1-e").parentElement!.querySelector(".accept-wait-line")).toBeTruthy();
    confirmed.unmount();
  });
});

describe("§7/§8 the shared popover lifecycle", () => {
  it("does not place a popover at the viewport origin when its anchor is hidden", () => {
    const wrapper = document.createElement("div");
    wrapper.hidden = true;
    const anchor = document.createElement("button");
    wrapper.append(anchor);
    document.body.append(wrapper);
    const onClose = vi.fn();
    render(<Popover anchor={anchor} label="隐藏浮层" onClose={onClose}>内容</Popover>);
    expect(screen.queryByRole("dialog", { name: "隐藏浮层" })).toBeNull();
    expect(onClose).toHaveBeenCalled();
    wrapper.remove();
  });

  function stubRects(anchorRect: () => { left: number; top: number; bottom: number }) {
    const own = Object.getOwnPropertyDescriptor(Element.prototype, "getBoundingClientRect");
    Element.prototype.getBoundingClientRect = function (this: Element) {
      if (this.classList.contains("shared-popover")) return { left: 0, top: 0, right: 200, bottom: 100, width: 200, height: 100, x: 0, y: 0, toJSON() {} } as DOMRect;
      if (this.id === "anchor") {
        const rect = anchorRect();
        return { ...rect, right: rect.left + 40, width: 40, height: rect.bottom - rect.top, x: rect.left, y: rect.top, toJSON() {} } as DOMRect;
      }
      return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON() {} } as DOMRect;
    };
    restorers.push(() => { if (own) Object.defineProperty(Element.prototype, "getBoundingClientRect", own); });
  }

  function Harness({ onClose }: { onClose: () => void }) {
    const anchor = useRef<HTMLButtonElement>(null);
    const [open, setOpen] = useState(false);
    return <>
      <button id="anchor" ref={anchor} onClick={() => setOpen(true)}>打开</button>
      {open && <Popover anchor={anchor.current} label="测试浮层" onClose={() => { onClose(); setOpen(false); }}>内容</Popover>}
    </>;
  }

  it("replays its placement on viewport resize and scroll, staying anchored and inside the viewport", async () => {
    let rect = { left: 700, top: 80, bottom: 100 };
    stubRects(() => rect);
    stubWindow("innerWidth", 1000);
    stubWindow("innerHeight", 800);
    const user = userEvent.setup();
    render(<Harness onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "打开" }));
    const dialog = screen.getByRole("dialog", { name: "测试浮层" });
    expect(dialog.style.left).toBe("700px");
    expect(dialog.style.top).toBe("104px");
    // Narrower viewport with an unchanged-size popup: clamped, not unplaced.
    act(() => { window.innerWidth = 600; window.dispatchEvent(new Event("resize")); });
    expect(dialog.style.left).toBe("392px");
    expect(dialog.style.top).toBe("104px");
    // A scroll that moves the anchor moves the popup with it.
    rect = { left: 120, top: 280, bottom: 300 };
    act(() => { document.dispatchEvent(new Event("scroll")); });
    expect(dialog.style.left).toBe("120px");
    expect(dialog.style.top).toBe("304px");
    // A short viewport flips it above the anchor.
    act(() => { window.innerHeight = 380; window.dispatchEvent(new Event("resize")); });
    expect(dialog.style.top).toBe("176px");
  });

  async function openHostEventPopup(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: /[▸▾] Host 事件/ }));
    const cluster = [...document.querySelectorAll<HTMLButtonElement>(".mk.cluster")][0]!;
    await user.click(cluster);
    return screen.getByRole("dialog", { name: /^Host 事件 \d+ 条$/ });
  }

  it("keeps one popover open at a time: opening another closes the Host event popup", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    function Page({ extra }: { extra: boolean }) {
      const anchor = useRef<HTMLButtonElement>(null);
      return <>
        <ObjectiveTimeline {...baseProps(timeline)} />
        <button ref={anchor}>筛选</button>
        {extra && <Popover anchor={anchor.current} label="筛选" onClose={vi.fn()}>项目</Popover>}
      </>;
    }
    const view = render(<Page extra={false} />);
    await openHostEventPopup(user);
    // Opened without any pointer press or focus move: the registry alone closes it.
    view.rerender(<Page extra />);
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /^Host 事件/ })).toBeNull());
    expect(screen.getByRole("dialog", { name: "筛选" })).toBeTruthy();
  });

  it("closes the Host event popup when focus leaves it, keeping its row keyboard navigation and sentences", async () => {
    const user = userEvent.setup();
    render(<ObjectiveTimeline {...baseProps(objectiveTimelineFixture())} />);
    const dialog = await openHostEventPopup(user);
    const rows = within(dialog).getAllByRole("button").filter(button => button.hasAttribute("data-row-index"));
    expect(rows.length).toBeGreaterThan(1);
    await waitFor(() => expect(document.activeElement).toBe(rows[0]));
    fireEvent.keyDown(rows[0]!, { key: "ArrowDown" });
    expect(document.activeElement).toBe(rows[1]);
    for (const row of rows) expect(row.getAttribute("aria-label")).toMatch(/[一-鿿]/);
    // Tab-like focus move out of the popup and its marker closes it.
    act(() => { screen.getByRole("button", { name: "列表" }).focus(); });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /^Host 事件/ })).toBeNull());
  });
});

describe("§9 the inspector drawer stays reachable", () => {
  function stubMedia(narrow: boolean) {
    const own = Object.getOwnPropertyDescriptor(window, "matchMedia");
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (query: string) => ({
        matches: query.includes("max-width") ? narrow : false,
        addEventListener: () => {}, removeEventListener: () => {},
      }),
    });
    restorers.push(() => {
      if (own) Object.defineProperty(window, "matchMedia", own);
      else Reflect.deleteProperty(window, "matchMedia");
    });
  }
  const separator = () => document.querySelector(".inspector-separator") as HTMLElement;
  const dock = () => document.querySelector(".inspector-dock") as HTMLElement;
  const valueNow = () => Number(separator().getAttribute("aria-valuenow"));

  it("starts at its title row on a narrow screen and opens to at most 40vh", async () => {
    stubMedia(true);
    stubWindow("innerHeight", 400);
    const user = userEvent.setup();
    render(<ObjectiveTimeline {...baseProps(objectiveTimelineFixture())} />);
    expect(dock().className).toContain("collapsed");
    expect(dock().style.height).toBe("44px");
    expect(document.querySelector(".timeline-inspector")).toBeNull();
    await user.click(within(dock()).getByRole("button", { name: "展开检查器" }));
    // 168px default, capped at 40% of a 400px viewport.
    expect(valueNow()).toBe(160);
    expect(separator().getAttribute("aria-valuemax")).toBe("160");
    expect(dock().style.height).toBe("160px");
    // The explicit choice holds: collapsing stays collapsed.
    await user.click(within(dock()).getByRole("button", { name: "收起检查器" }));
    expect(dock().style.height).toBe("44px");
  });

  it("updates the narrow summary after selection while the drawer stays folded", async () => {
    stubMedia(true);
    stubWindow("innerHeight", 400);
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const pinned: InspectorSelection = { type: "run", runId: "r1" };
    const view = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    expect(dock().className).toContain("collapsed");
    view.rerender(<ObjectiveTimeline {...baseProps(timeline, { selection: pinned })} />);
    expect(dock().className).toContain("collapsed");
    expect(dock().querySelector(".inspector-dock-title")!.textContent).toContain("整项委派");
    await user.click(within(dock()).getByRole("button", { name: "展开检查器" }));
    expect(document.querySelector(".timeline-inspector")).toBeTruthy();
    await user.click(within(dock()).getByRole("button", { name: "收起检查器" }));
    view.rerender(<ObjectiveTimeline {...baseProps(timeline, { selection: { type: "run", runId: "r2" } })} />);
    expect(dock().className).toContain("collapsed");
  });

  it("re-clamps on column resize so the timeline keeps 200px, folds when only a sliver is left, and restores the user's choice", async () => {
    stubMedia(false);
    const observers: { callback: ResizeObserverCallback; targets: Element[] }[] = [];
    vi.stubGlobal("ResizeObserver", class {
      targets: Element[] = [];
      constructor(public callback: ResizeObserverCallback) { observers.push(this); }
      observe(target: Element) { this.targets.push(target); }
      unobserve() {}
      disconnect() {}
    });
    let column = 700;
    const sections: Record<string, number> = { "tl-head": 120, "delegation-band": 28, "tl-toolbar": 40, "inspector-separator": 10 };
    const sectionHeight = (element: HTMLElement) => {
      for (const [name, height] of Object.entries(sections)) if (element.classList.contains(name)) return height;
      return 0;
    };
    stubGetter(HTMLElement.prototype, "clientHeight", function (this: HTMLElement) {
      return this.classList.contains("timeline-view") ? column : 0;
    });
    stubGetter(HTMLElement.prototype, "offsetHeight", function (this: HTMLElement) { return sectionHeight(this); });
    stubGetter(Element.prototype, "scrollHeight", function (this: HTMLElement) { return sectionHeight(this); });
    const user = userEvent.setup();
    render(<ObjectiveTimeline {...baseProps(objectiveTimelineFixture())} />);
    await user.click(within(dock()).getByRole("button", { name: "展开检查器" }));
    const root = document.querySelector(".timeline-view")!;
    // The card-band budget also observes the column; target the drawer's
    // observer, which observes the timeline body as well as fixed sections.
    const drawerObserver = observers.find(observer => observer.targets.includes(root)
      && observer.targets.some(target => target.matches(".timeline-body, .tl-body")))!;
    const resize = (height: number) => {
      column = height;
      act(() => { drawerObserver.callback([], drawerObserver as unknown as ResizeObserver); });
    };
    // Plenty of room: the 168px default.
    resize(700);
    expect(valueNow()).toBe(168);
    // The reserved sections take 198px; 520 − 198 − 200 leaves 122px.
    resize(520);
    expect(valueNow()).toBe(122);
    expect(dock().className).not.toContain("collapsed");
    // An explicitly opened drawer retains a usable body in a short column.
    resize(450);
    expect(dock().className).not.toContain("collapsed");
    expect(valueNow()).toBe(96);
    // Growing back restores the user's height instead of the clamp.
    fireEvent.keyDown(separator(), { key: "ArrowDown" });
    resize(900);
    expect(valueNow()).toBe(80);
    fireEvent.keyDown(separator(), { key: "Enter" });
    expect(valueNow()).toBe(168);
    // The header grows (for example the band expands): the drawer yields.
    sections["delegation-band"] = 200;
    resize(700);
    expect(valueNow()).toBe(700 - 370 - 200);
  });
});

describe("§10/§11 the list filter popup", () => {
  const rows = listFixture();
  const listProps = (overrides: Record<string, unknown> = {}) => ({
    rows, total: rows.length, loading: false, error: "", nextCursor: null, reorder: null,
    filter: "all" as const, query: "", projectId: "", hostId: "",
    choices: { projects: [{ id: "p1", label: "hey-my-buddy", path: null }], hosts: ["codex-desktop"] },
    selected: null, rail: false, active: true,
    onFilterChange: vi.fn(), onQueryChange: vi.fn(), onProjectChange: vi.fn(), onHostChange: vi.fn(),
    onSelect: vi.fn(), onRefresh: vi.fn(), onRetry: vi.fn(), onMore: vi.fn(), onApplyReorder: vi.fn(),
    ...overrides,
  });

  it("focuses the 项目 select when opened from the keyboard", async () => {
    const user = userEvent.setup();
    render(<ObjectiveList {...listProps()} />);
    const button = screen.getByRole("button", { name: "筛选" });
    button.focus();
    await user.keyboard("{Enter}");
    const dialog = screen.getByRole("dialog", { name: "筛选" });
    expect(button.getAttribute("aria-controls")).toBe(dialog.id);
    // The first dropdown, not the status buttons or any informational text.
    const [project] = within(dialog).getAllByRole("combobox");
    expect(project!.closest("label")!.textContent).toMatch(/^项目/);
    await waitFor(() => expect(document.activeElement).toBe(project));
  });

  it("closes when the rail hides the list or the view is inactive, and does not reopen on return", async () => {
    const user = userEvent.setup();
    const view = render(<ObjectiveList {...listProps()} />);
    await user.click(screen.getByRole("button", { name: "筛选" }));
    expect(screen.getByRole("dialog", { name: "筛选" })).toBeTruthy();
    view.rerender(<ObjectiveList {...listProps({ rail: true })} />);
    expect(screen.queryByRole("dialog", { name: "筛选" })).toBeNull();
    view.rerender(<ObjectiveList {...listProps({ rail: false })} />);
    expect(screen.queryByRole("dialog", { name: "筛选" })).toBeNull();
    expect(screen.getByRole("button", { name: "筛选" }).getAttribute("aria-expanded")).toBe("false");

    await user.click(screen.getByRole("button", { name: "筛选" }));
    expect(screen.getByRole("dialog", { name: "筛选" })).toBeTruthy();
    view.rerender(<ObjectiveList {...listProps({ active: false })} />);
    expect(screen.queryByRole("dialog", { name: "筛选" })).toBeNull();
    view.rerender(<ObjectiveList {...listProps({ active: true })} />);
    expect(screen.queryByRole("dialog", { name: "筛选" })).toBeNull();
  });
});
