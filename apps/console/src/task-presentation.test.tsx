import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "i18next";
import { MacroTaskOverview } from "./MacroTaskOverview";
import { TaskDetail } from "./task-detail/TaskDetail";
import { createTaskDetailClient } from "./task-detail/client";
import { task as microTask, workflow, audit } from "./task-detail/task-detail.fixtures";
import { macroTaskTimelineFixture } from "./macro-task-fixtures";
import type { ConsoleApi } from "./api";

afterEach(async () => { cleanup(); vi.restoreAllMocks(); await i18n.changeLanguage("zh"); });

const runId = "79771ce9-890b-4cab-867b-4e05c68791da";

describe("task identifiers and recorded attribution", () => {
  it.each(["macro-", "run:"])("copies the complete %s identity from macro task details", async prefix => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    const summary = { ...macroTaskTimelineFixture().macroTask, macroTaskId: prefix + runId,
      kind: prefix === "run:" ? "standalone" as const : "macro-task" as const };
    render(<MacroTaskOverview summary={summary} timeline={null} loading={false} stale={false} onBackToList={vi.fn()} />);
    const header = screen.getByRole("heading", { name: summary.title! }).closest("header")!;
    await user.click(within(header).getByRole("button", { name: "时间统计" }));
    await user.click(within(screen.getByRole("dialog", { name: "时间统计" })).getByRole("button", { name: `复制标识 ${prefix}${runId}` }));
    expect(write).toHaveBeenCalledWith(prefix + runId);
  });

  it("copies a raw runId from micro task details", async () => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    const task = microTask({ runId });
    const api = { task: vi.fn(async () => task), command: vi.fn(async (operation: string, params: { includeAudit?: boolean }) => operation === "message_list" ? { messages: [] } : {
      ...workflow({ runId }), ...(params.includeAudit ? { audit: audit(), routingHistory: { entries: [], nextCursor: null } } : {}),
    }) } as unknown as ConsoleApi;
    render(<TaskDetail client={createTaskDetailClient(api, "fixture")} runId={runId} timeline={null} timelineLoading={false}
      nowMs={Date.parse("2026-10-15T01:00:00Z")} step={1} origin={null} active onStep={vi.fn()} onBack={vi.fn()} onOpenRun={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: `复制标识 ${runId}` }));
    expect(write).toHaveBeenCalledWith(runId);
  });

});
