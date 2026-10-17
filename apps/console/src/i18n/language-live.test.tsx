import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { getLanguageChoice, setLanguageChoice } from "./index";
import { MacroTaskList } from "../MacroTaskList";
import { TooltipProvider } from "../components/ui/tooltip";
import { formatList, formatNumber } from "./format";
import { outcomeLabel, rowStateInfo } from "../macro-task-display";
import { taskTitle } from "../task-state";
import { useEditor } from "../use-editor";
import type { ConsoleApi } from "../api";
import type { Snapshot, Task } from "../types";
import type { TimelineRow, TimelineSpan } from "../macro-task-types";

afterEach(() => { cleanup(); setLanguageChoice("browser"); });

describe("changing the language in an already loaded console", () => {
  it("updates the mounted macro task list and previously imported helper vocabulary without reloading", () => {
    render(<TooltipProvider><MacroTaskList rows={[]} totals={{ all: 0, active: 0, host: 0, review: 0 }}
      loading={false} error="" nextCursor={null} reorder={null} filter="all" query="" selected={null}
      onCollapse={() => {}} onFilterChange={() => {}} onQueryChange={() => {}}
      onSelect={() => {}} onMore={() => {}} onApplyReorder={() => {}} /></TooltipProvider>);
    expect(screen.getByText("没有宏任务")).toBeTruthy();
    act(() => setLanguageChoice("en"));
    expect(screen.getByText("No macro tasks")).toBeTruthy();
    expect(getLanguageChoice()).toBe("en");
    expect(outcomeLabel({ kind: "execution" } as TimelineSpan, "unknown")).toBe("Stop unconfirmed");
    expect(rowStateInfo({ state: "accepted" } as TimelineRow).label).toBe("Accepted");
    expect(taskTitle({ task: "" } as Task).text).toBe("Unnamed micro task");
    expect(formatNumber(12345.6)).toBe(new Intl.NumberFormat("en").format(12345.6));
    expect(formatList(["Codex", "Claude"])).toBe(new Intl.ListFormat("en", { type: "conjunction" }).format(["Codex", "Claude"]));
    act(() => setLanguageChoice("zh"));
    expect(screen.getByText("没有宏任务")).toBeTruthy();
    expect(rowStateInfo({ state: "accepted" } as TimelineRow).label).toBe("已验收");
    expect(taskTitle({ task: "" } as Task).text).toBe("未命名微任务");
  });

  it("translates a notice already shown by the editor while retaining its draft state", async () => {
    const snapshot = { eventCursor: 8, csrfToken: "csrf", consoleSession: { id: "session", canWrite: true, reason: null },
      buddies: [], preferences: [], modelServiceNotes: [], modelServiceConcurrency: [], configuration: null,
      decisions: [], tasks: { pendingCount: 0 }, capabilities: {} } as Snapshot;
    const { result } = renderHook(() => useEditor({} as ConsoleApi, snapshot, async () => snapshot, true, ""));
    await act(async () => result.current.discard());
    expect(result.current.notice).toBe("草稿已放弃");
    act(() => setLanguageChoice("en"));
    expect(result.current.notice).toBe("Draft discarded");
    expect(result.current.draft).toBeNull();
  });

});
