import { TooltipProvider } from "./components/ui/tooltip";
import type { ReactElement } from "react";
import { cleanup, render as baseRender } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MacroTaskList } from "./MacroTaskList";
import { listFixture } from "./macro-task-fixtures";

afterEach(() => cleanup());
describe("macro task list keyboard scrolling", () => {
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
    expect(scroll.closest("[data-slot=scroll-area]")).toBeTruthy();
    expect(scroll.closest("[data-slot=macro-list]")).toBeTruthy();
    expect(scroll.tabIndex).toBe(0);
    scroll.focus();
    expect(document.activeElement).toBe(scroll);
    const entries = scroll.querySelectorAll("[data-slot=macro-group-heading], [data-slot=macro-entry]");
    expect(entries.length).toBeGreaterThan(0);
    expect(scroll.querySelectorAll("[data-slot=macro-entry]").length).toBeGreaterThan(0);
  });
});

function render(ui: ReactElement) { return baseRender(ui, { wrapper: TooltipProvider }); }
