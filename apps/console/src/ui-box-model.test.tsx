import { TooltipProvider } from "./components/ui/tooltip";
import type { ReactElement } from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render as baseRender } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MacroTaskList } from "./MacroTaskList";
import { listFixture } from "./macro-task-fixtures";
import { Status } from "./ui";

// Check the shared box model and keyboard scrolling for controls that remain.
let injected: HTMLStyleElement;
beforeEach(() => {
  injected = document.createElement("style");
  injected.textContent = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
  document.head.append(injected);
});
afterEach(() => { injected.remove(); cleanup(); });
function rule(selector: string) {
  const matches = [...injected.sheet!.cssRules].filter((rule): rule is CSSStyleRule => "selectorText" in rule)
    .filter(rule => rule.selectorText.split(",").map(s => s.trim()).includes(selector));
  expect(matches.length, selector).toBeGreaterThan(0);
  return matches.at(-1)!.style;
}
describe("U5: audited controls retain the shared global box model and normal sizes", () => {
  it("keeps the rendered list viewport focusable for keyboard scrolling", () => {
    const rows = listFixture();
    render(<MacroTaskList
      rows={rows} totals={{all:rows.length,active:1,host:1,review:1}} onCollapse={vi.fn()} loading={false} error="" nextCursor={null} reorder={null}
      filter="all" query=""
      selected={null}
      onFilterChange={vi.fn()} onQueryChange={vi.fn()}
      onSelect={vi.fn()} onMore={vi.fn()} onApplyReorder={vi.fn()}
    />);
    const scroll = document.querySelector<HTMLElement>("[data-slot=macro-list] [data-slot=scroll-area-viewport]")!;
    expect(scroll).toBeTruthy();
    expect(scroll.getAttribute("aria-label")).toBe("宏任务条目");
    const css = getComputedStyle(scroll);
    expect(["auto", "scroll"]).toContain(css.overflowY);
    expect(css.overflowX).toBe("hidden");
    expect(scroll.closest("[data-slot=scroll-area]")).toBeTruthy();
    expect(scroll.closest("[data-slot=macro-list]")).toBeTruthy();
    expect(scroll.tabIndex).toBe(0);
    scroll.focus();
    expect(document.activeElement).toBe(scroll);
    const entries = scroll.querySelectorAll("[data-slot=macro-group-heading], [data-slot=macro-entry]");
    expect(entries.length).toBeGreaterThan(0);
    expect(scroll.querySelectorAll("[data-slot=macro-entry]").length).toBeGreaterThan(0);
  });

  it("uses the existing global border-box reset for badges and inputs", () => {
    const { container } = render(<>
      <Status status="waiting-host" />
      <input type="checkbox" />
    </>);
    for (const element of container.querySelectorAll<HTMLElement>(".badge, input")) {
      expect(getComputedStyle(element).boxSizing, element.className).toBe("border-box");
    }
    const badge = getComputedStyle(container.querySelector(".badge")!);
    expect(badge.padding).toBe("2px 6px");
    expect(badge.lineHeight).not.toBe("1");
    expect(getComputedStyle(container.querySelector("input")!).width).toBe("16px");
    expect(rule("*").boxSizing).toBe("border-box");
  });
});

function render(ui: ReactElement) { return baseRender(ui, { wrapper: TooltipProvider }); }
