import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "i18next";
import { MacroTaskOverview } from "./MacroTaskOverview";
import { TaskDetail } from "./task-detail/TaskDetail";
import { createTaskDetailClient } from "./task-detail/client";
import { Tasks } from "./Tasks";
import { TimelineInspector } from "./TimelineInspector";
import { macroTaskTimelineFixture } from "./macro-task-fixtures";
import { rowLabelItem, spanFacts } from "./macro-task-display";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";

afterEach(async () => { cleanup(); vi.restoreAllMocks(); await i18n.changeLanguage("zh"); });

const runId = "79771ce9-890b-4cab-867b-4e05c68791da";
const snapshot = { csrfToken: "fixture", eventCursor: 0, buddies: [] } as unknown as Snapshot;
function commandTask(): Task {
  return { runId, task: "Run checks", status: "cancelled", owner: null, cwd: "~/project", revision: 1,
    createdAt: "2026-10-15T00:00:00Z", updatedAt: "2026-10-15T00:00:00Z", acceptedAt: null, acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null,
    spec: { adapter: "command", argv: ["npm", "test"] },
    activity: { phase: "waiting-host", waitingReason: "the run was cancelled" },
    lineage: { kind: "execution", sourceHostId: null, currentHostId: null, rootRunId: null, rootLeadRunId: null, parentRunId: null,
      project: { id: "project", label: "project", path: "~/project" }, buddy: null } };
}
function apiFor(task: Task): ConsoleApi {
  return { task: vi.fn(async () => task), tasks: vi.fn(async () => ({ runs: [task], total: 1, nextCursor: null })),
    command: vi.fn() } as unknown as ConsoleApi;
}

describe("task identifiers and recorded attribution", () => {
  it.each(["macro-", "run:"])("copies the complete %s identity beside the macro task title", async prefix => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    const summary = { ...macroTaskTimelineFixture().macroTask, macroTaskId: prefix + runId,
      kind: prefix === "run:" ? "standalone" as const : "macro-task" as const };
    render(<MacroTaskOverview summary={summary} timeline={null} loading={false} stale={false} onBackToList={vi.fn()} />);
    const header = screen.getByRole("heading", { name: summary.title }).closest("header")!;
    await user.click(within(header).getByRole("button", { name: `复制标识 ${prefix}${runId}` }));
    expect(write).toHaveBeenCalledWith(prefix + runId);
  });

  it("copies a raw runId from micro task details", async () => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    const task = commandTask();
    render(<TaskDetail client={createTaskDetailClient(apiFor(task), "fixture")} runId={runId} timeline={null} timelineLoading={false}
      nowMs={Date.parse("2026-10-15T01:00:00Z")} step={1} origin={null} active backTo="records" onStep={vi.fn()} onBack={vi.fn()} onOpenRun={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: `复制标识 ${runId}` }));
    expect(write).toHaveBeenCalledWith(runId);
  });

  it("copies the selected run from inspector spans, whole tasks and retained facts", async () => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    const timeline = macroTaskTimelineFixture();
    const span = timeline.spans.find(span => span.spanId === "s-r1-e")!;
    const item = spanFacts(span, timeline.rows[0], Date.parse(timeline.observedAt)).item;
    const props = { selection: { type: "item" as const, key: item.key }, timeline, itemsByKey: new Map([[item.key, item]]),
      truncatedEvents: false, onOpen: vi.fn(), onSelectItem: vi.fn(), onSelectRun: vi.fn(), onUnpin: vi.fn() };
    const view = render(<TimelineInspector {...props} />);
    await user.click(screen.getByRole("button", { name: "复制标识 r1" }));
    expect(write).toHaveBeenLastCalledWith("r1");
    view.rerender(<TimelineInspector {...props} timeline={{ ...timeline, spans: [] }} itemsByKey={new Map()} />);
    expect(screen.getByText("记录在当前范围外")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "复制标识 r1" }));
    expect(write).toHaveBeenLastCalledWith("r1");
    const row = timeline.rows.find(row => row.runId !== "r1")!;
    const rowItem = rowLabelItem(row);
    view.rerender(<TimelineInspector {...props} selection={{ type: "run", runId: row.runId }}
      itemsByKey={new Map([[rowItem.key, rowItem]])} />);
    await user.click(screen.getByRole("button", { name: `复制标识 ${row.runId}` }));
    expect(write).toHaveBeenLastCalledWith(row.runId);
    expect(props.onOpen).not.toHaveBeenCalled();
    expect(props.onSelectRun).not.toHaveBeenCalled();
  });

  it("omits a command's buddy in the execution list and details, and does not show the blackboard's waiting sentence", async () => {
    const user = userEvent.setup();
    const task = commandTask();
    const view = render(<Tasks snapshot={snapshot} api={apiFor(task)} refresh={vi.fn()} onOpenMicroTask={vi.fn()} />);
    const row = await screen.findByRole("button", { name: /Run checks/ });
    expect(within(row).getByText("未记录委派方").getAttribute("title")).toBe("未记录委派方");
    expect(row.textContent).not.toMatch(/undefined|buddy|→/);
    await user.click(row);
    const band = await waitFor(() => {
      const found = view.container.querySelector("[data-slot=detail-band]");
      if (!found) throw new Error("no band yet");
      return found;
    });
    // The band of a command names the command, not a buddy.
    expect(band.textContent).toContain("命令执行");
    expect(view.container.querySelector(".detail-panel")!.textContent).not.toMatch(/undefined|执行 buddy 待确定|Worker/);
    // The sentence the blackboard gives for why it waits is English and belongs to the board: the detail does not show it.
    expect(screen.queryByText(/the run was cancelled/)).toBeNull();
  });
});
