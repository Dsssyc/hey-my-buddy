import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Objectives } from "./Objectives";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task, TaskQuery } from "./types";
import { objectiveTimelineFixture, OBSERVED_AT } from "./objective-fixtures";
import { VIEW_PREFERENCE_PREFIX } from "./view-preferences";
import { readFileSync } from "node:fs";

function snapshotFixture(): Snapshot {
  return {
    csrfToken: "csrf", consoleSession: { id: "session-a", canWrite: true, reason: null }, tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, routerProfileIds: [], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    profiles: [], cards: [], preferences: [], familyPreferences: [], preferenceOverrides: [], familyAnnotations: [], evidence: [], decisions: [],
    sampleCounts: {}, modelConcurrency: [], tasks: { pendingCount: 0 },
    capabilities: { evaluationWriteGate: true },
  };
}

const rawRecord = (): Task => ({ runId: "cmd-9", task: "外部命令记录", status: "completed", owner: "fixture",
  cwd: "/worktrees/cmd-9", revision: 1, createdAt: "2026-09-26T05:00:00Z", acceptedAt: null, acceptanceVerdict: null,
  delegation: { kind: "execution", sourceHostId: null, currentHostId: null, parentRunId: null, rootRunId: "cmd-9",
    project: { id: "px", label: "外部", path: null }, configuration: null } });

function harness(paged = false) {
  const snapshot = snapshotFixture();
  const timeline = objectiveTimelineFixture();
  const api = {
    snapshot: vi.fn(async () => structuredClone(snapshot)),
    command: vi.fn(),
    task: vi.fn(async (runId: string) => ({ ...rawRecord(), runId })),
    tasks: vi.fn(async ({ rootsOnly }: TaskQuery) => ({ runs: rootsOnly ? [] : [rawRecord()], total: 1, nextCursor: null })),
    objectives: vi.fn(async () => ({ objectives: [timeline.objective], total: 1, nextCursor: paged ? "older" : null, cursor: 41, changed: false })),
    objectiveTimeline: vi.fn(async () => ({ timeline, verifiedAtMs: null })),
  } as unknown as ConsoleApi;
  const refresh = vi.fn(async () => structuredClone(snapshot));
  const user = userEvent.setup();
  const view = render(<Objectives snapshot={snapshot} api={api} refresh={refresh} active />);
  return { ...view, api, refresh, user, timeline };
}

afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });


const preference = VIEW_PREFERENCE_PREFIX + "delegation-list-collapsed";
const objectiveRow = () => screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ });
function objectiveRail() {
  expect(screen.queryByRole("region", { name: "工作目标列表（已收起）" })).not.toBeNull();
  return screen.getByRole("region", { name: "工作目标列表（已收起）" });
}
function narrow(matches: boolean) {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
}
async function openDetail(f: ReturnType<typeof harness>) {
  if (!document.querySelector(".timeline-view")) {
    await f.user.click(await screen.findByRole("button", { name: /工作目标时间轴：设计、接口与实现/ }));
  }
  await waitFor(() => expect(document.querySelector('[data-key="span:s-r1-e"]')).toBeTruthy());
  await f.user.dblClick(document.querySelector('[data-key="span:s-r1-e"]') as HTMLElement);
  await waitFor(() => expect(document.querySelector(".run-view")).toBeTruthy());
}

describe("U08 delegation navigation", () => {
  it("keeps title-left switches and command records accessible by keyboard without a top switch row", async () => {
    const f = harness();
    await screen.findByRole("button", { name: /工作目标时间轴：设计、接口与实现/ });
    expect(screen.queryByRole("button", { name: "切换到全部执行记录" })).not.toBeNull();
    const button = screen.getByRole("button", { name: "切换到全部执行记录" });
    expect(button.getAttribute("title")).toBe("切换到全部执行记录");
    expect(button.nextElementSibling?.tagName).toBe("H2");
    expect(document.querySelector(".history-switch-bar")).toBeNull();
    button.focus();
    await f.user.keyboard("{Enter}");
    const back = screen.getByRole("button", { name: "切换到工作目标" });
    expect(back.getAttribute("title")).toBe("切换到工作目标");
    expect(back.nextElementSibling?.textContent).toBe("全部执行记录");
    await f.user.click(screen.getByLabelText("显示协助任务与内部执行"));
    expect(await screen.findByRole("button", { name: /外部命令记录/ })).toBeTruthy();
    back.focus();
    await f.user.keyboard(" ");
    expect(objectiveRow()).toBeTruthy();
    expect(screen.getByLabelText("显示协助任务与内部执行").closest("[hidden]")).toBeTruthy();
  });

  it("preserves manual collapse, mounted list scroll and pagination through view/page changes and reload", async () => {
    const f = harness(true);
    await screen.findByRole("button", { name: /工作目标时间轴：设计、接口与实现/ });
    const list = screen.getByLabelText("工作目标条目");
    list.scrollTop = 123;
    const search = screen.getByLabelText("搜索工作目标");
    const more = screen.getByRole("button", { name: "加载更早工作目标" });
    await f.user.click(screen.getByRole("button", { name: "收起工作目标列表" }));
    expect(localStorage.getItem(preference)).toBe("true");
    expect(list.closest("[hidden]")).toBeTruthy();
    expect(list.scrollTop).toBe(123);
    expect(screen.getByLabelText("搜索工作目标")).toBe(search);
    expect(within(objectiveRail()).queryByRole("button", { name: "切换到全部执行记录" })).not.toBeNull();
    await f.user.click(within(objectiveRail()).getByRole("button", { name: "切换到全部执行记录" }));
    const rawRail = screen.getByRole("region", { name: "全部执行记录列表（已收起）" });
    expect(within(rawRail).getByRole("button", { name: "展开全部执行记录列表" })).toBeTruthy();
    await f.user.click(within(rawRail).getByRole("button", { name: "切换到工作目标" }));
    f.rerender(<Objectives snapshot={snapshotFixture()} api={f.api} refresh={f.refresh} active={false} />);
    f.rerender(<Objectives snapshot={snapshotFixture()} api={f.api} refresh={f.refresh} active />);
    expect(objectiveRail()).toBeTruthy();
    await f.user.click(within(objectiveRail()).getByRole("button", { name: "展开工作目标列表" }));
    expect(screen.getByLabelText("工作目标条目")).toBe(list);
    expect(list.scrollTop).toBe(123);
    expect(screen.getByRole("button", { name: "加载更早工作目标" })).toBe(more);
    await f.user.click(more);
    expect(f.api.objectives).toHaveBeenCalledWith(expect.objectContaining({ before: "older", limit: 50 }), expect.any(AbortSignal));
    await f.user.click(screen.getByRole("button", { name: "收起工作目标列表" }));
    f.unmount();
    const next = harness();
    expect(objectiveRail()).toBeTruthy();
    await next.user.click(within(objectiveRail()).getByRole("button", { name: "展开工作目标列表" }));
    expect(localStorage.getItem(preference)).toBe("false");
    expect(screen.getByRole("region", { name: "工作目标列表" })).toBeTruthy();
  });

  it("auto detail collapse never writes a habit and manual collapse survives closing the detail", async () => {
    narrow(false);
    const f = harness();
    await openDetail(f);
    expect(objectiveRail()).toBeTruthy();
    expect(localStorage.getItem(preference)).toBeNull();
    await f.user.click(within(document.querySelector(".locator") as HTMLElement).getByRole("button", { name: "关闭详情" }));
    expect(screen.getByRole("region", { name: "工作目标列表" })).toBeTruthy();
    await f.user.click(within(screen.getByRole("region", { name: "工作目标列表" })).getByRole("button", { name: "收起工作目标列表" }));
    await openDetail(f);
    await f.user.click(within(document.querySelector(".locator") as HTMLElement).getByRole("button", { name: "关闭详情" }));
    expect(objectiveRail()).toBeTruthy();
    expect(localStorage.getItem(preference)).toBe("true");
    await f.user.click(within(objectiveRail()).getByRole("button", { name: "展开工作目标列表" }));
    expect(screen.getByRole("region", { name: "工作目标列表" })).toBeTruthy();
    expect(localStorage.getItem(preference)).toBe("false");
  });

  it("manual expansion while a detail is docked opens the existing drawer and clears only the manual habit", async () => {
    narrow(false);
    const f = harness();
    await openDetail(f);
    await f.user.click(within(objectiveRail()).getByRole("button", { name: "展开工作目标列表" }));
    const drawer = await screen.findByRole("dialog", { name: "工作目标列表（抽屉）" });
    await f.user.click(within(drawer).getByRole("button", { name: "收起工作目标列表" }));
    expect(localStorage.getItem(preference)).toBe("true");
    await f.user.click(within(objectiveRail()).getByRole("button", { name: "展开工作目标列表" }));
    expect(await screen.findByRole("dialog", { name: "工作目标列表（抽屉）" })).toBeTruthy();
    expect(localStorage.getItem(preference)).toBe("false");
    expect(document.querySelector(".right-dock.side")).toBeTruthy();
  });

  it("uses the standard safe preference behavior when browser storage is blocked", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    const f = harness();
    await f.user.click(screen.getByRole("button", { name: "收起工作目标列表" }));
    expect(objectiveRail()).toBeTruthy();
    await f.user.click(within(objectiveRail()).getByRole("button", { name: "展开工作目标列表" }));
    expect(screen.getByRole("region", { name: "工作目标列表" })).toBeTruthy();
  });

  it("offers the switch in the narrow detail title and keeps a manually collapsed rail reachable", async () => {
    narrow(true);
    const f = harness();
    await f.user.click(await screen.findByRole("button", { name: /工作目标时间轴：设计、接口与实现/ }));
    const mobile = document.querySelector(".list-mobile-navigation") as HTMLElement;
    expect(within(mobile).queryByRole("button", { name: "切换到全部执行记录" })).not.toBeNull();
    const toggle = within(mobile).getByRole("button", { name: "切换到全部执行记录" });
    expect(toggle.getAttribute("title")).toBe("切换到全部执行记录");
    await f.user.click(within(mobile).getByRole("button", { name: "收起工作目标列表" }));
    expect(objectiveRail()).toBeTruthy();
    const css = readFileSync("src/styles.css", "utf8");
    expect(css).toContain(".workspace-grid.list-rail.has-selection > .rail-panel { display: flex; }");
    expect(css).toContain(".workspace-grid.list-rail { display: grid; }");
    toggle.focus();
    await f.user.keyboard("{Enter}");
    expect(screen.getByRole("region", { name: "全部执行记录列表（已收起）" })).toBeTruthy();
  });
});
