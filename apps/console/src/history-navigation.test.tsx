import { cleanup, render as baseRender, screen, waitFor, within } from "@testing-library/react";
import type { RenderOptions } from "@testing-library/react";
import type { ReactElement } from "react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MacroTasks } from "./MacroTasks";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import { task as detailTask, workflow as detailWorkflow, audit as detailAudit } from "./task-detail/task-detail.fixtures";
import { macroTaskTimelineFixture } from "./macro-task-fixtures";
import { VIEW_PREFERENCE_PREFIX } from "./view-preferences";
import { TooltipProvider } from "./components/ui/tooltip";

function render(ui: ReactElement, options?: RenderOptions) {
  return baseRender(ui, { wrapper: TooltipProvider, ...options });
}

function snapshotFixture(): Snapshot {
  return {
    csrfToken: "csrf", eventCursor: 0, consoleSession: { id: "session-a", canWrite: true, reason: null },

    configuration: { routerBuddyIds: [], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    buddies: [], preferences: [], modelServiceNotes: [], decisions: [],
 modelServiceConcurrency: [], tasks: { pendingCount: 0 },
    capabilities: {},
  };
}


function harness(paged = false) {
  const snapshot = snapshotFixture();
  const timeline = macroTaskTimelineFixture();
  const api = {
    snapshot: vi.fn(async () => structuredClone(snapshot)),
    command: vi.fn(async (operation: string, params: { runId: string; includeAudit?: boolean }) => {
      if (operation === "message_list") return { messages: [] };
      if (operation === "workflow_get") return { ...detailWorkflow({ runId: params.runId }),
        ...(params.includeAudit ? { audit: detailAudit(), routingHistory: { entries: [], nextCursor: null, total: 0 } } : {}) };
      throw new Error(`Unexpected operation: ${operation}`);
    }),
    task: vi.fn(async (runId: string) => detailTask({ runId,
      lineage: { ...detailTask().lineage!, rootRunId: runId },
      workflow: { ...detailTask().workflow!, macroTaskId: timeline.macroTask.macroTaskId } })),
    macroTasks: vi.fn(async () => ({ macroTasks: [timeline.macroTask], totals: { all: 1, active: 1, host: 0, review: 0 }, nextCursor: paged ? "older" : null, cursor: 41, changed: false })),
    macroTaskTimeline: vi.fn(async () => ({ timeline, verifiedAtMs: null })),
  } as unknown as ConsoleApi;
  const refresh = vi.fn(async () => structuredClone(snapshot));
  const user = userEvent.setup();
  const view = render(<MacroTasks snapshot={snapshot} api={api} refresh={refresh} active />);
  return { ...view, api, refresh, user, timeline };
}

afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "#"); });


const preference = VIEW_PREFERENCE_PREFIX + "macro-task-list-collapsed";
function narrow(matches: boolean) {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
}
async function openDetail(f: ReturnType<typeof harness>) {
  if (!document.querySelector("[data-slot=task-work]")) await f.user.click(await screen.findByRole("button", { name: /宏任务时间轴：设计、接口与实现/ }));
  await waitFor(() => expect(document.querySelector('[data-key="span:s-r1-e"]')).toBeTruthy());
  await f.user.dblClick(document.querySelector('[data-key="span:s-r1-e"]') as HTMLElement);
  await screen.findByRole("region", { name: "微任务详情" });
}

describe("macro task navigation", () => {
  it("removes the whole list column when collapsed and retains its query and pagination after expansion", async () => {
    const f = harness(true);
    await screen.findByRole("button", { name: /宏任务时间轴：设计、接口与实现/ });
    await f.user.type(within(screen.getByRole("region", { name: "宏任务列表" })).getByLabelText("搜索标题、项目、Host 或编号"), "设计");
    await f.user.click(screen.getByRole("button", { name: "收起宏任务列表" }));
    expect(localStorage.getItem(preference)).toBe("true");
    expect(screen.queryByRole("region", { name: "宏任务列表" })).toBeNull();
    expect(document.querySelector('[data-slot="macro-sidebar"] [data-slot="macro-list"]')).toBeNull();
    await f.user.click(screen.getByRole("button", { name: "展开宏任务列表" }));
    expect(within(screen.getByRole("region", { name: "宏任务列表" })).getByLabelText("搜索标题、项目、Host 或编号")).toHaveProperty("value", "设计");
    await f.user.click(await screen.findByRole("button", { name: "更早" }));
    expect(f.api.macroTasks).toHaveBeenCalledWith(expect.objectContaining({ before: "older", limit: 50 }), expect.any(AbortSignal));
    await f.user.click(screen.getByRole("button", { name: "收起宏任务列表" }));
    f.unmount();
    const next = harness();
    expect(screen.queryByRole("region", { name: "宏任务列表" })).toBeNull();
    await next.user.click(await screen.findByRole("button", { name: "展开宏任务列表" }));
    expect(localStorage.getItem(preference)).toBe("false");
    expect(screen.getByRole("region", { name: "宏任务列表" })).toBeTruthy();
  });

  it("keeps the list open when a detail opens and the manual collapse through detail navigation", async () => {
    narrow(false);
    const f = harness();
    await openDetail(f);
    expect(screen.getByRole("region", { name: "宏任务列表" })).toBeTruthy();
    expect(localStorage.getItem(preference)).toBeNull();
    await f.user.click(screen.getByRole("button", { name: "收起宏任务列表" }));
    expect(localStorage.getItem(preference)).toBe("true");
    expect(screen.queryByRole("region", { name: "宏任务列表" })).toBeNull();
    await f.user.keyboard("{Escape}");
    await openDetail(f);
    await f.user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "展开宏任务列表" })).toBeTruthy();
    expect(localStorage.getItem(preference)).toBe("true");
    await f.user.click(screen.getByRole("button", { name: "展开宏任务列表" }));
    expect(screen.getByRole("region", { name: "宏任务列表" })).toBeTruthy();
    expect(localStorage.getItem(preference)).toBe("false");
  });

  it("keeps collapse usable when browser storage is blocked", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    const f = harness();
    await f.user.click(screen.getByRole("button", { name: "收起宏任务列表" }));
    expect(screen.queryByRole("region", { name: "宏任务列表" })).toBeNull();
    await f.user.click(screen.getByRole("button", { name: "展开宏任务列表" }));
    expect(screen.getByRole("region", { name: "宏任务列表" })).toBeTruthy();
  });

  it("ignores the retired micro task list preference", async () => {
    localStorage.setItem(VIEW_PREFERENCE_PREFIX + "micro-task-list-collapsed", "true");
    harness();
    expect(screen.getByRole("region", { name: "宏任务列表" })).toBeTruthy();
    expect(localStorage.getItem(preference)).toBeNull();
  });

  it("offers expansion in the narrow macro header without a residual rail", async () => {
    narrow(true);
    const f = harness();
    await f.user.click(await screen.findByRole("button", { name: /宏任务时间轴：设计、接口与实现/ }));
    await f.user.click(screen.getByRole("button", { name: "收起宏任务列表" }));
    const header = document.querySelector('[data-slot="macro-overview-header"]') as HTMLElement;
    expect(within(header).getByRole("button", { name: "展开宏任务列表" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "宏任务列表（已收起）" })).toBeNull();
    await f.user.click(within(header).getByRole("button", { name: "展开宏任务列表" }));
    expect(screen.getByRole("region", { name: "宏任务列表" })).toBeTruthy();
  });
});
