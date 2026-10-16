import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { getLanguageChoice, setLanguageChoice } from "./index";
import { ConfirmDialog } from "../ConfirmDialog";
import { formatList, formatNumber } from "./format";
import { outcomeLabel, rowStateInfo, spanFacts, spanHead } from "../macro-task-display";
import { TimelineInspector } from "../TimelineInspector";
import { macroTaskTimelineFixture } from "../macro-task-fixtures";
import { taskTitle } from "../task-state";
import { useEditor } from "../use-editor";
import type { ConsoleApi } from "../api";
import type { Snapshot, Task } from "../types";
import type { TimelineRow, TimelineSpan } from "../macro-task-types";

afterEach(() => { cleanup(); setLanguageChoice("browser"); });

describe("changing the language in an already loaded console", () => {
  it("updates mounted legacy controls and previously imported helper vocabulary without reloading", () => {
    render(<ConfirmDialog title="Example" confirmLabel="Continue" onCancel={() => {}} onConfirm={() => {}}>Example</ConfirmDialog>);
    expect(screen.getByRole("button", { name: "取消" })).toBeTruthy();
    act(() => setLanguageChoice("en"));
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(getLanguageChoice()).toBe("en");
    expect(outcomeLabel({ kind: "execution" } as TimelineSpan, "unknown")).toBe("Stop unconfirmed");
    expect(spanHead("host")).toBe("Waiting for Host");
    expect(rowStateInfo({ state: "accepted" } as TimelineRow).label).toBe("Accepted");
    expect(taskTitle({ task: "" } as Task).text).toBe("Unnamed micro task");
    expect(formatNumber(12345.6)).toBe(new Intl.NumberFormat("en").format(12345.6));
    expect(formatList(["Codex", "Claude"])).toBe(new Intl.ListFormat("en", { type: "conjunction" }).format(["Codex", "Claude"]));
    act(() => setLanguageChoice("zh"));
    expect(screen.getByRole("button", { name: "取消" })).toBeTruthy();
    expect(spanHead("host")).toBe("等待 Host");
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

  it("rebuilds a missing pinned record in the new language from its retained evidence", () => {
    const timeline = macroTaskTimelineFixture();
    const span = timeline.spans.find(candidate => candidate.kind === "execution" && candidate.resultStatus === "ok")!;
    const row: TimelineRow = { ...timeline.rows.find(candidate => candidate.runId === span.runId)!, title: "Retained task", titleSource: "title" };
    const retained = { ...timeline, rows: [row], spans: [span], events: [] };
    const { item } = spanFacts(span, row, Date.parse(timeline.observedAt));
    const props = { selection: { type: "item" as const, key: item.key }, timeline: retained,
      itemsByKey: new Map([[item.key, item]]), truncatedEvents: false,
      onOpen: () => {}, onSelectItem: () => {}, onSelectRun: () => {}, onUnpin: () => {} };
    const { container, rerender } = render(<TimelineInspector {...props} />);
    expect(screen.getByText("Retained task")).toBeTruthy();
    rerender(<TimelineInspector {...props} timeline={{ ...retained, rows: [], spans: [] }} itemsByKey={new Map()} />);
    expect(screen.getByText("记录在当前范围外")).toBeTruthy();
    act(() => setLanguageChoice("en"));
    expect(screen.getByText("Record outside the current scope")).toBeTruthy();
    expect(screen.getByText("Retained task")).toBeTruthy();
    expect(screen.getByText("Execution completed")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Open details" }).getAttribute("aria-disabled")).toBe("true");
    expect([...container.querySelectorAll(".inspector-field-label")].map(node => node.textContent)).toContain("Result");
    expect([...container.querySelectorAll(".inspector-field-label")].some(node => /[\u4e00-\u9fff]/.test(node.textContent ?? ""))).toBe(false);
  });
});
