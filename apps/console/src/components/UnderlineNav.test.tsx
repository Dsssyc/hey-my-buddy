import { act, cleanup, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UnderlineNav } from "./UnderlineNav";
import { Tabs, TabsList, TabsTrigger } from "./ui/tabs";

const sizes = { first: { left: 0, width: 60 }, second: { left: 80, width: 100 } };
const resizes: Array<{ disconnect: ReturnType<typeof vi.fn> }> = [];

beforeEach(() => {
  resizes.length = 0;
  vi.stubGlobal("ResizeObserver", class {
    disconnect = vi.fn();
    observe = vi.fn();
    unobserve = vi.fn();
    constructor() { resizes.push(this); }
  });
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
  // jsdom has no pseudo-element layout. Browser checks cover the actual ink box.
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation(node => computedStyle(node));
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function measuredLayout() {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const size = sizes[this.textContent as keyof typeof sizes] ?? { left: 0, width: 200 };
    return { ...size, x: size.left, y: 0, top: 0, bottom: 32, right: size.left + size.width, height: 32, toJSON() {} };
  });
}

function Navigation({ active }: { active: string }) {
  return <UnderlineNav aria-label="Sections">
    {["first", "second"].map(name => <a key={name} href={`#${name}`} aria-current={active === name ? "page" : undefined}>{name}</a>)}
  </UnderlineNav>;
}

describe("underline navigation", () => {
  it("mounts and switches without layout measurements", async () => {
    const view = render(<Navigation active="first" />);
    await act(async () => { view.rerender(<Navigation active="second" />); });
    expect(screen.getByRole("link", { name: "second" }).hasAttribute("data-ink-glide")).toBe(false);
  });

  it("glides on aria-current changes and clears the transform evidence at animation end", async () => {
    measuredLayout();
    const view = render(<Navigation active="first" />);
    expect(view.container.querySelector("[data-ink-glide]")).toBeNull();
    await act(async () => { view.rerender(<Navigation active="second" />); });
    const active = screen.getByRole("link", { name: "second" });
    expect(active.getAttribute("data-ink-glide")).toBe("run");
    expect(active.style.getPropertyValue("--ink-dx")).toBe("-80px");
    expect(active.style.getPropertyValue("--ink-sx")).toBe("0.6");
    act(() => { active.dispatchEvent(new Event("animationend")); });
    expect(active.hasAttribute("data-ink-glide")).toBe(false);
    expect(active.style.getPropertyValue("--ink-dx")).toBe("");
  });

  it("disconnects both observers and clears an in-flight glide when unmounted", async () => {
    measuredLayout();
    const disconnect = vi.spyOn(MutationObserver.prototype, "disconnect");
    const view = render(<Navigation active="first" />);
    await act(async () => { view.rerender(<Navigation active="second" />); });
    const active = screen.getByRole("link", { name: "second" });
    view.unmount();
    expect(disconnect).toHaveBeenCalledOnce();
    expect(resizes).toHaveLength(1);
    expect(resizes[0].disconnect).toHaveBeenCalledOnce();
    expect(active.hasAttribute("data-ink-glide")).toBe(false);
    expect(active.style.getPropertyValue("--ink-sx")).toBe("");
  });
});

describe("line tabs", () => {
  function LineTabs({ value, listRef }: { value: string; listRef?: React.Ref<HTMLDivElement> }) {
    return <Tabs value={value}><TabsList variant="line" ref={listRef} aria-label="Filters">
      <TabsTrigger value="first">first</TabsTrigger><TabsTrigger value="second">second</TabsTrigger>
    </TabsList></Tabs>;
  }

  it("forwards the list ref, glides on data-state changes, and disconnects its observers", async () => {
    measuredLayout();
    const disconnect = vi.spyOn(MutationObserver.prototype, "disconnect");
    const listRef = createRef<HTMLDivElement>();
    const view = render(<LineTabs value="first" listRef={listRef} />);
    expect(listRef.current).toBe(screen.getByRole("tablist"));
    expect(view.container.querySelector("[data-ink-glide]")).toBeNull();
    await act(async () => { view.rerender(<LineTabs value="second" listRef={listRef} />); });
    const active = screen.getByRole("tab", { name: "second" });
    expect(active.getAttribute("data-state")).toBe("active");
    expect(active.getAttribute("data-ink-glide")).toBe("run");
    view.unmount();
    expect(listRef.current).toBeNull();
    expect(disconnect).toHaveBeenCalled();
    expect(resizes).toHaveLength(1);
    expect(resizes[0].disconnect).toHaveBeenCalledOnce();
    expect(active.hasAttribute("data-ink-glide")).toBe(false);
  });

  it("keeps both navigation forms still when reduced motion is requested", async () => {
    measuredLayout();
    vi.mocked(window.matchMedia).mockReturnValue({ matches: true } as MediaQueryList);
    const view = render(<><Navigation active="first" /><LineTabs value="first" /></>);
    await act(async () => { view.rerender(<><Navigation active="second" /><LineTabs value="second" /></>); });
    expect(view.container.querySelector("[data-ink-glide]")).toBeNull();
    expect(screen.getByRole("tab", { name: "second" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("link", { name: "second" }).getAttribute("aria-current")).toBe("page");
  });
});
