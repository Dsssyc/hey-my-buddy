import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { Help } from "./ui";

/**
 * The `?` explanation is portalled to `document.body` and positioned fixed, so
 * a container's `overflow` and the window edge can no longer clip it. These
 * checks mock the button and panel rects because jsdom has no layout.
 */

const restorers: (() => void)[] = [];
afterEach(() => {
  cleanup();
  while (restorers.length) restorers.pop()!();
});

const rects = {
  button: { left: 0, top: 100, width: 20, height: 20 },
  tip: { width: 300, height: 40 },
};

function stubRects() {
  const own = Object.getOwnPropertyDescriptor(Element.prototype, "getBoundingClientRect");
  const toRect = (left: number, top: number, width: number, height: number) =>
    ({ left, top, right: left + width, bottom: top + height, width, height, x: left, y: top, toJSON() {} }) as DOMRect;
  Element.prototype.getBoundingClientRect = function (this: Element) {
    if (this.classList.contains("help-button")) return toRect(rects.button.left, rects.button.top, rects.button.width, rects.button.height);
    if (this.classList.contains("help-tip")) return toRect(0, 0, rects.tip.width, rects.tip.height);
    return toRect(0, 0, 0, 0);
  };
  restorers.push(() => { if (own) Object.defineProperty(Element.prototype, "getBoundingClientRect", own); });
}

function stubViewport(width: number, height: number) {
  for (const [name, value] of [["innerWidth", width], ["innerHeight", height]] as const) {
    const own = Object.getOwnPropertyDescriptor(window, name);
    Object.defineProperty(window, name, { configurable: true, writable: true, value });
    restorers.push(() => { if (own) Object.defineProperty(window, name, own); });
  }
}

function renderHelp() {
  const view = render(
    <div className="clipper" style={{ overflow: "hidden", width: 40 }}>
      <Help label="档位说明">档位说明：点开关即启用或停用该档位。</Help>
    </div>,
  );
  return { clipper: view.container.querySelector(".clipper")!, button: screen.getByRole("button", { name: "档位说明" }) };
}

function tipBox() {
  const tip = screen.getByRole("tooltip");
  return { tip, left: Number.parseFloat(tip.style.left), top: Number.parseFloat(tip.style.top) };
}

function insideViewport(left: number, top: number, width = rects.tip.width) {
  expect(left).toBeGreaterThanOrEqual(8);
  expect(left + width).toBeLessThanOrEqual(window.innerWidth - 8);
  expect(top).toBeGreaterThanOrEqual(8);
  expect(top + rects.tip.height).toBeLessThanOrEqual(window.innerHeight - 8);
}

describe("the `?` explanation layer", () => {
  it("renders through a body portal, outside the clipping container, fully inside the left edge", () => {
    stubRects();
    stubViewport(1024, 768);
    rects.button = { left: 0, top: 100, width: 20, height: 20 };
    rects.tip = { width: 300, height: 40 };
    const { clipper, button } = renderHelp();
    fireEvent.focus(button);
    const { tip, left, top } = tipBox();
    // The fixed portal is not inside the overflow-hidden ancestor.
    expect(clipper.contains(tip)).toBe(false);
    expect(tip.parentElement).toBe(document.body);
    expect(tip.style.position || getComputedStyle(tip).position).not.toBe("absolute");
    // Centring under a button at x=0 would put it at -140; it clamps to the margin.
    expect(left).toBe(8);
    expect(top).toBe(126);
    insideViewport(left, top);
  });

  it("shifts a near-right explanation back inside the viewport", () => {
    stubRects();
    stubViewport(1024, 768);
    rects.button = { left: 1004, top: 100, width: 20, height: 20 };
    rects.tip = { width: 300, height: 40 };
    renderHelp();
    fireEvent.focus(screen.getByRole("button", { name: "档位说明" }));
    const { left, top } = tipBox();
    // Centring would end at 1164; the panel is pulled back to the right margin.
    expect(left).toBe(716);
    insideViewport(left, top);
  });

  it("flips above the button when the space below is too small", () => {
    stubRects();
    stubViewport(1024, 768);
    rects.button = { left: 500, top: 740, width: 20, height: 20 };
    rects.tip = { width: 300, height: 40 };
    renderHelp();
    fireEvent.focus(screen.getByRole("button", { name: "档位说明" }));
    const { left, top } = tipBox();
    expect(top + rects.tip.height).toBeLessThanOrEqual(rects.button.top);
    insideViewport(left, top);
  });

  it("caps the inline size to the viewport so long text wraps", () => {
    stubRects();
    stubViewport(400, 768);
    rects.button = { left: 10, top: 100, width: 20, height: 20 };
    rects.tip = { width: 600, height: 40 };
    renderHelp();
    fireEvent.focus(screen.getByRole("button", { name: "档位说明" }));
    const { tip, left, top } = tipBox();
    expect(tip.style.maxWidth).toBe("384px");
    expect(left).toBe(8);
    insideViewport(left, top, 384);
    // The stylesheet keeps the fixed layer and normal wrapping as the fallback.
    const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    const start = styles.indexOf(".help-tip {");
    expect(start).toBeGreaterThan(-1);
    const rule = styles.slice(start, styles.indexOf("}", start));
    expect(rule).toMatch(/position:\s*fixed/);
    expect(rule).toMatch(/max-inline-size:/);
    expect(rule).toMatch(/white-space:\s*normal/);
    expect(rule).toMatch(/overflow-wrap:\s*anywhere/);
  });

  it("shows on hover, hides on mouse leave, and Escape dismisses without moving focus", async () => {
    stubRects();
    stubViewport(1024, 768);
    rects.button = { left: 200, top: 100, width: 20, height: 20 };
    rects.tip = { width: 300, height: 40 };
    const user = userEvent.setup();
    const { button } = renderHelp();
    await user.hover(button);
    expect(screen.getByRole("tooltip")).toBeTruthy();
    await user.unhover(button);
    expect(screen.queryByRole("tooltip")).toBeNull();
    await user.click(button);
    expect(screen.getByRole("tooltip")).toBeTruthy();
    fireEvent.keyDown(button, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(document.activeElement).toBe(button);
  });
});
