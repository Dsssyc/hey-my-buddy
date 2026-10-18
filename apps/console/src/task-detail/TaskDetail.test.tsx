import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { CODEX, HOST, OPUS, at, client, event, inquiry, row, routing, span, task, timeline, workflow } from "./task-detail.fixtures";
import { chosenStep, open, pane, ready, step, strip, stripSlice } from "./task-detail.testing";
import type { Artifact, Integration } from "./types";

afterEach(cleanup);

describe("opening the detail of a micro task", () => {
  it("reads the micro task, then chooses the step: the last turn of a delivered micro task, and says so to the address", async () => {
    const f = open();
    await ready();
    expect(f.reads.task).toHaveBeenCalledWith("m1", expect.any(AbortSignal));
    expect(f.reads.workflow).toHaveBeenCalledWith("m1");
    expect(f.reads.audit).toHaveBeenCalledWith("m1", ["decision-1"]);
    expect(f.reads.inquiries).toHaveBeenCalledWith("m1");
    expect(chosenStep()).toContain("回合 2");
    // The steps sit in the strip along the top: this micro task's own timeline row.
    expect(strip()).toBeTruthy();
    expect(stripSlice("span:e2")!.getAttribute("aria-pressed")).toBe("true");
    expect(stripSlice("span:e1")!.getAttribute("aria-pressed")).toBe("false");
    expect(f.onStep).toHaveBeenCalledExactlyOnceWith(7);
  });

  it("opens on the step the address names, and replaces an address whose step does not exist", async () => {
    const named = open({ step: 3 });
    await ready();
    expect(chosenStep()).toContain("回合 1");
    expect(named.onStep).not.toHaveBeenCalled();
    cleanup();
    const gone = open({ step: 99 });
    await ready();
    expect(chosenStep()).toContain("回合 2");
    expect(gone.onStep).toHaveBeenCalledExactlyOnceWith(7);
  });

  it("opens on the step that the timeline's span or marker it came from belongs to", async () => {
    const fromRoute = open({ origin: "span:r1" });
    await ready();
    expect(chosenStep()).toContain("路由");
    cleanup();
    const fromWait = open({ origin: "span:h1" });
    await ready();
    expect(pane().textContent).toContain("需要读取样例目录");
    cleanup();
    open({ origin: "settle:m1" });
    await ready();
    expect(chosenStep()).toContain("验收");
    expect(fromRoute.onStep).toHaveBeenCalledExactlyOnceWith(2);
    expect(fromWait.onStep).toHaveBeenCalledExactlyOnceWith(4);
  });

  it("waits for the timeline before it lists the steps, and lists the delegation alone, with a note, when the timeline has no row for it", async () => {
    const waiting = open({ timeline: null, timelineLoading: true });
    expect(await screen.findByRole("status")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "这个微任务的各步" })).toBeNull();
    expect(waiting.onStep).not.toHaveBeenCalled();
    cleanup();
    const cut = timeline();
    cut.rows = [];
    open({ timeline: cut });
    await ready();
    expect(document.querySelector("[data-slot=detail-strip]")).toBeNull();
    expect(document.querySelector("[data-slot=task-detail]")!.textContent).toContain("没有这个微任务的一行");
    expect(pane().textContent).toContain("实现微任务详情");
  });

  it("goes back by Escape, unless the key is for a field or a dialog, and not while the page is elsewhere", async () => {
    const f = open();
    await ready();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(f.onBack).toHaveBeenCalledTimes(1);
    const field = document.body.appendChild(document.createElement("input"));
    fireEvent.keyDown(field, { key: "Escape" });
    const dialog = document.body.appendChild(document.createElement("div"));
    dialog.setAttribute("role", "dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(f.onBack).toHaveBeenCalledTimes(1);
    field.remove();
    dialog.remove();
    cleanup();
    const away = open({ active: false });
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(away.onBack).not.toHaveBeenCalled();
  });

  it("switches detail steps with horizontal arrows, clamps both ends and focuses the step's own slice", async () => {
    const f = open({ step: 1 });
    await ready();
    await f.user.keyboard("{ArrowLeft}"); expect(chosenStep()).toContain("委派");
    await f.user.keyboard("{ArrowRight}"); expect(chosenStep()).toContain("路由");
    expect(document.activeElement?.getAttribute("data-key")).toBe("span:r1");
    await f.user.keyboard("{ArrowLeft}"); expect(chosenStep()).toContain("委派");
    for (let press = 0; press < 10; press += 1) await f.user.keyboard("{ArrowRight}");
    expect(chosenStep()).toContain("验收");
    expect(document.activeElement?.getAttribute("aria-pressed")).toBe("true");
    await f.user.keyboard("{ArrowLeft}"); expect(document.activeElement?.getAttribute("data-key")).toBe("span:e2");
    const field = document.body.appendChild(document.createElement("input")); field.focus();
    // A key for a field is not a key for the detail: the step stays where the reader left it.
    await f.user.keyboard("{ArrowLeft}"); expect(chosenStep()).toContain("回合 2"); field.remove();
  });

  it("shows a read that failed as a failure with a retry that reads again", async () => {
    const reads = client();
    (reads.task as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new ApiError("NOT_FOUND", "Unknown runId"));
    const f = open({ client: reads });
    expect((await screen.findByRole("alert")).textContent).toContain("Unknown runId");
    expect(screen.getByRole("heading", { name: "读取微任务失败" })).toBeTruthy();
    expect(screen.queryByRole("group", { name: "这个微任务的各步" })).toBeNull();
    await f.user.click(screen.getByRole("button", { name: "重试读取" }));
    await ready();
    expect(reads.task).toHaveBeenCalledTimes(2);
  });
});

describe("the head of the detail", () => {
  it("names the micro task, its state in the fixed word, when it began, how long, and how many turns", async () => {
    open();
    await ready();
    const head = document.querySelector("[data-slot=detail-header]") as HTMLElement;
    expect(within(head).getByRole("heading", { name: "微任务详情" })).toBeTruthy();
    expect(head.textContent).toContain("等待验收");
    expect(head.textContent).toContain("回合2");
    expect(head.textContent).toMatch(/开始.*历时/);
    const started = within(head).getByText("开始").nextElementSibling;
    expect(started?.textContent).toMatch(/\d{2}:\d{2}$/);
    expect(started?.textContent).not.toMatch(/:\d{2}:\d{2}$/);
    expect(head.querySelector("[data-slot=detail-status]")?.parentElement?.querySelector("button[aria-label=重新读取]")).toBeTruthy();
  });

  it("falls back to the first line of the task for a title, and says so", async () => {
    const untitled = task();
    delete (untitled.workflow as { title?: string }).title;
    open({ replies: { task: untitled } });
    await ready();
    const title = document.querySelector("[data-slot=detail-header] h2") as HTMLElement;
    expect(title.textContent).toBe("实现微任务详情，左边是步骤，右边是这一步的内容。");
    await userEvent.setup().hover(title);
    expect((await screen.findAllByText(/取自任务首行/))[0]).toBeTruthy();
  });

  it("writes a wait for a Lead's decision as a wait for the Lead, in the head and on the rail", async () => {
    const awaiting = workflow({ state: "awaiting-host", activeRequest: { requestId: "req-2", kind: "assistance", state: "open", createdAt: at("11:00"), decidedAt: null,
      childTaskId: null, routing: false, summary: "要不要改范围", reason: null, attempted: null, neededWork: [], expectedArtifacts: [], acceptance: null, preparationError: null },
    counts: { turns: 2, openRequests: 1 } });
    const leadTimeline = timeline();
    leadTimeline.rows = [row({ state: "awaiting-host", rootLeadRunId: "lead-1" })];
    leadTimeline.spans = leadTimeline.spans.filter(item => item.spanId !== "e2" && item.spanId !== "q2");
    leadTimeline.spans.push(span("h2", "host", at("11:00"), null, { state: "open", requestId: "req-2", requestKind: "assistance" }));
    leadTimeline.events = leadTimeline.events.filter(item => item.kind === "dispatch");
    open({ timeline: leadTimeline, replies: { workflow: awaiting, task: task({ workflow: { ...task().workflow, state: "awaiting-host" }, lineage: { ...task().lineage, rootLeadRunId: "lead-1" } }) } });
    await ready();
    expect(document.querySelector("[data-slot=detail-header]")!.textContent).toContain("等待 Lead");
    expect(pane().textContent).toContain("等待 Lead");
    expect(stripSlice("span:h2")!.getAttribute("aria-pressed")).toBe("true");
    expect(pane().textContent).not.toContain("等待 Host");
  });

  it("marks a micro task that waits for acceptance, one that is running and one that failed with their own words", async () => {
    for (const [state, status, word] of [["accepted", "completed", "已验收"], ["failed", "failed", "失败"], ["executing", "running", "进行中"], ["concluded", "completed", "已结束"]] as const) {
      cleanup();
      open({ replies: { workflow: workflow({ state }), task: task({ status, workflow: { ...task().workflow, state }, activeAttemptId: null }) } });
      await ready();
      expect(document.querySelector("[data-slot=detail-header]")!.textContent, state).toContain(word);
    }
  });

  it("tells that a turn is past its expected time or silent only while it is the one running", async () => {
    const reminders = { overdue: true, possiblyStuck: true, lastNativeEventAt: null, toolInProgress: false };
    const running = task({ status: "running", activeAttemptId: "att-2", selectedAttempt: { attemptId: "att-2", reminders } });
    const live = timeline();
    Object.assign(live.spans.find(item => item.spanId === "e2")!, { endAt: null, state: "executing", shutdownConfirmed: false });
    open({ timeline: live, replies: { task: running, workflow: workflow({ state: "executing" }) } });
    await ready();
    const head = document.querySelector("[data-slot=detail-header]") as HTMLElement;
    expect(head.textContent).toContain("已超过预期");
    expect(head.textContent).toContain("可能卡住");
    expect(pane().textContent).toContain("已超过预期");
    cleanup();
    // The same record on a micro task whose attempt is no longer running warns of nothing.
    open({ replies: { task: task({ status: "completed", activeAttemptId: null, selectedAttempt: { attemptId: "att-2", reminders } }) } });
    await ready();
    expect(document.querySelector("[data-slot=detail-header]")!.textContent).not.toContain("可能卡住");
  });

  it("reads the micro task again by its button, with the server's time of checking in the hint", async () => {
    const f = open();
    await ready();
    const button = screen.getByRole("button", { name: "重新读取" });
    await f.user.hover(button);
    expect((await screen.findAllByText(/核对时间 \d{2}:\d{2}:\d{2}/))[0]).toBeTruthy();
    const before = (f.reads.task as ReturnType<typeof vi.fn>).mock.calls.length;
    await f.user.click(button);
    await waitFor(() => expect((f.reads.task as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(before));
  });

  it("keeps what was read when a later read fails, says so, and reads again on retry", async () => {
    const reads = client();
    const f = open({ client: reads });
    await ready();
    (reads.workflow as ReturnType<typeof vi.fn>).mockRejectedValue(new ApiError("NETWORK", "连接中断"));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 3100)); });
    expect((await screen.findByRole("alert")).textContent).toContain("连接中断");
    expect(chosenStep()).toContain("回合 2");
    (reads.workflow as ReturnType<typeof vi.fn>).mockImplementation(async () => ({ value: workflow(), verifiedAtMs: 1 }));
    await f.user.click(within(screen.getByRole("alert")).getByRole("button", { name: "重试读取" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  }, 10000);
});

describe("the delegation", () => {
  it("is the task, where it came from and the micro tasks around it", async () => {
    const long = "第一段。".repeat(120);
    const helper = workflow({ children: [{ taskId: "h1", role: "helper", state: "succeeded" }, { taskId: "d1", role: "router", state: "succeeded" }], delegationAuthority: true });
    const withFixes = timeline();
    withFixes.rows = [row({ fixes: "m0", fixesTitle: "被修正的微任务", fixesTitleSource: "title", fixedBy: ["m2"] }), row({ runId: "m0", title: "被修正的微任务" }), row({ runId: "m2", title: "后来的修正" }), row({ runId: "h1", title: "协助查路径" })];
    const f = open({ step: 1, timeline: withFixes, replies: { task: task({ task: long, lineage: { ...task().lineage, parentRunId: "m0" } }), workflow: helper } });
    await ready();
    const text = pane().textContent!;
    expect(text).toContain("原始委派方");
    expect(text).toContain("codex-desktop");
    expect(text).toContain("hey-my-buddy");
    expect(text).toContain("/work/m1");
    expect(text).toContain("带委派权");
    // A long task is folded and unfolds.
    expect(text).not.toContain(long);
    await f.user.click(within(pane()).getByRole("button", { name: "展开" }));
    await waitFor(() => expect(pane().textContent).toContain(long));
    // The id copies; the other micro tasks open at their own detail.
    expect(within(pane()).getByRole("button", { name: /复制标识 m1/ })).toBeTruthy();
    await f.user.click(within(pane()).getByRole("button", { name: "协助查路径" }));
    expect(f.onOpenRun).toHaveBeenLastCalledWith("h1");
    expect(within(pane()).queryByRole("button", { name: "路由计算" })).toBeNull();
    await f.user.click(within(pane()).getAllByRole("button", { name: "被修正的微任务" })[0]!);
    expect(f.onOpenRun).toHaveBeenLastCalledWith("m0");
    expect(pane().textContent).toContain("被修正 1 次");
    await f.user.click(within(pane()).getByRole("button", { name: "后来的修正" }));
    expect(f.onOpenRun).toHaveBeenLastCalledWith("m2");
  });
});

describe("the route", () => {
  it("names the Router, how it was asked, what it chose and why", async () => {
    open({ step: 2 });
    await ready();
    const band = pane().querySelector("[data-slot=detail-band]") as HTMLElement;
    expect(band.textContent).toContain("Router");
    expect(band.textContent).toContain("codex / openai / gpt-6-luna / high");
    expect(band.textContent).toContain("快速");
    expect(pane().textContent).toContain("codex / openai / gpt-6.1-sol / high");
    expect(pane().textContent).toContain("近期同类微任务里它一次通过");
    // The list of candidates and their preferences is not shown.
    expect(pane().textContent).not.toMatch(/候选|偏好/);
  });

  it("says what the record lacks is unrecorded, and fills in nothing from today's facts", async () => {
    open({ step: 2, replies: { routing: [routing({ routerBuddy: null, routingMode: null, selectedBuddy: null, reason: null })] }, timeline: (() => {
      const bare = timeline();
      bare.spans.find(item => item.spanId === "r1")!.routing = null;
      return bare;
    })() });
    await ready();
    const band = pane().querySelector("[data-slot=detail-band]") as HTMLElement;
    expect(band.textContent).toContain("未记录");
    expect(pane().textContent).not.toContain("gpt-6-luna");
    expect(pane().textContent!.match(/未记录/g)!.length).toBeGreaterThanOrEqual(3);
  });

  it("says a choice by the program was not a model's, and a route that failed or abstained is not a success", async () => {
    open({ step: 2, replies: { routing: [routing({ source: "single-candidate" })] } });
    await ready();
    expect(pane().textContent).toContain("程序直选");
    expect(pane().textContent).toContain("没有调用 Router");
    cleanup();
    const failed = timeline();
    Object.assign(failed.spans.find(item => item.spanId === "r1")!, { resultStatus: "failed", error: "路由边界被拒绝" });
    open({ step: 2, timeline: failed });
    await ready();
    expect(pane().textContent).toContain("失败");
    expect(pane().textContent).toContain("路由边界被拒绝");
  });

  it("folds a long reason, unfolds it in the same paragraph, and starts folded again on another route", async () => {
    const reason = "具体的选择依据。".repeat(60);
    const two = timeline();
    two.spans.find(item => item.spanId === "r1")!.routing!.note = reason;
    two.spans.push(span("r2", "routing", at("10:40"), at("10:41"), { attemptId: "att-r2", decisionId: "decision-2", routing: { routingMode: "fast", selectedBuddy: CODEX, reason: null, note: reason + "二" } }));
    const f = open({ step: 2, timeline: two, replies: { routing: [routing(), routing({ decisionId: "decision-2" })] } });
    await ready();
    const paragraphs = () => [...pane().querySelectorAll("p")].filter(node => node.textContent!.includes("具体的选择依据"));
    expect(paragraphs()).toHaveLength(1);
    expect(paragraphs()[0]!.textContent).not.toBe(reason);
    await f.user.click(within(pane()).getByRole("button", { name: "展开" }));
    await waitFor(() => expect(paragraphs()[0]!.textContent).toBe(reason));
    expect(paragraphs()).toHaveLength(1);
    expect(within(pane()).getByRole("button", { name: "收起" }).getAttribute("aria-expanded")).toBe("true");
    await f.user.click(within(pane()).getByRole("button", { name: "收起" }));
    await waitFor(() => expect(within(pane()).getByRole("button", { name: "展开" })).toBeTruthy());
    await f.user.click(within(pane()).getByRole("button", { name: "展开" }));
    fireEvent.click(stripSlice("span:r2")!);
    await waitFor(() => expect(within(pane()).getByRole("button", { name: "展开" }).getAttribute("aria-expanded")).toBe("false"));
  });
});

describe("a turn", () => {
  it("opens with the task, then what happened in it, then its result and what is left, then its seal", async () => {
    const artifact: Artifact = { artifactId: "art-1", kind: "output", attemptId: "att-1", manifestSha256: "f".repeat(64), outputCommit: "3f9a2c1d00", diffPath: "/diffs/1.diff",
      changedPaths: ["a.ts", "b.ts"], partial: false, verified: null, final: null, cumulativePatch: { baseCommit: "d286db0aa", outputCommit: "3f9a2c1d00", path: "/p.patch", sha256: "a".repeat(64), changedPaths: ["a.ts"] } };
    const integration: Integration = { integrationId: "int-1", artifactId: "art-1", state: "not-required", strategy: "none", target: null, beforeCommit: null, afterCommit: null,
      verificationSummary: null, hostPaths: ["docs/console.md"], note: "未整合", actor: HOST, createdAt: at("11:05") };
    const f = open({ step: 3, replies: { workflow: workflow({ artifacts: [artifact], integrations: [integration] }), inquiries: { items: [
      inquiry({ messageId: "q1", question: "读到哪里了？", state: "answered", answer: { text: "读到第二个文件。", at: at("10:21"), truncated: false } }),
      inquiry({ messageId: "q2", question: "收到请回复", state: "unavailable", reason: { code: "inquiry-no-live-agent", params: {} }, answer: null, createdAt: at("10:31") }),
      inquiry({ messageId: "q3", attemptId: "att-2", question: "第二轮的问询", state: "queued" }),
    ], more: false } } });
    await ready();
    const text = () => pane().textContent!;
    expect(text()).toContain("回合 1");
    expect(text()).toContain("实现微任务详情，左边是步骤");
    expect(text()).toContain("读到哪里了？");
    expect(text()).toContain("已答复");
    expect(text()).toContain("读到第二个文件。");
    expect(text()).toContain("无法送达");
    expect(text()).not.toContain("第二轮的问询");
    await waitFor(() => expect(text()).toContain("第一轮：读完了旧的详情"));
    // The band of the seal: kind, short commit, base to output, files; it opens to the evidence.
    const seal = within(pane()).getByRole("button", { name: /封存/ });
    expect(seal.textContent).toContain("3f9a2c1");
    expect(seal.textContent).toContain("d286db0 → 3f9a2c1");
    expect(seal.textContent).toContain("文件 2");
    await f.user.click(seal);
    await waitFor(() => expect(text()).toContain("art-1"));
    expect(text()).toContain("f".repeat(64));
    expect(text()).toContain("/diffs/1.diff");
    expect(text()).toContain("累计补丁");
    expect(text()).toContain("docs/console.md");
    expect(text()).toContain("无需整合");
    // The reason an inquiry could not be delivered is in its hint.
    await f.user.hover(within(pane()).getByText("无法送达"));
    expect((await screen.findAllByText("没有可接收问询的活动代理。"))[0]).toBeTruthy();
  });

  it("begins a later turn with what the Host gave it to continue, and keeps each turn's own result", async () => {
    open({ step: 7 });
    await ready();
    expect(pane().textContent).toContain("继续。读取样例目录已获批准");
    expect(pane().textContent).not.toContain("实现微任务详情，左边是步骤");
    expect(pane().textContent).toContain("第二轮：详情页已经按回合重做。");
    expect(pane().textContent).toContain("浏览器检查还要补两项");
    expect(pane().textContent).not.toContain("第一轮：读完了旧的详情");
  });

  it("writes a result that has not been read yet as loading, and a missing one as unrecorded", async () => {
    const f = open({ step: 3 });
    await ready();
    await waitFor(() => expect(pane().textContent).toContain("第一轮：读完了旧的详情"));
    expect(f.reads.audit).toHaveBeenCalled();
    cleanup();
    const slow = client();
    (slow.audit as ReturnType<typeof vi.fn>).mockImplementation(() => new Promise(() => undefined));
    open({ step: 3, client: slow });
    await ready();
    expect(pane().textContent).toContain("正在读取");
  });

  it("says from whom the buddy came: the Host's own pick, the user's lock, the only candidate, or nothing recorded", async () => {
    open({ step: 7 });
    await ready();
    expect(pane().querySelector("[data-slot=detail-band]")!.textContent).toContain("Host 指定");
    cleanup();
    open({ step: 7, replies: { workflow: workflow({ buddyLocked: true }) } });
    await ready();
    expect(pane().querySelector("[data-slot=detail-band]")!.textContent).toContain("用户锁定");
    cleanup();
    open({ step: 3, replies: { routing: [routing({ source: "single-candidate" })] } });
    await ready();
    expect(pane().querySelector("[data-slot=detail-band]")!.textContent).toContain("唯一候选");
    cleanup();
    const bare = workflow();
    bare.turns = [bare.turns[0]!, { ...bare.turns[1]!, routing: null }];
    open({ step: 3, replies: { workflow: bare } });
    await ready();
    expect(pane().querySelector("[data-slot=detail-band]")!.textContent).toContain("未记录路由");
  });

  it("writes the usage of this execution alone, the cache inside the input, and what was not recorded as unknown, never as nought", async () => {
    open({ step: 3 });
    await ready();
    const band = pane().querySelector("[data-slot=detail-band]") as HTMLElement;
    expect(band.textContent).toContain("输入 23.4k");
    expect(band.textContent).toContain("输出 3.1k");
    expect(band.textContent).toContain("缓存 18.0k");
    const user = userEvent.setup();
    await user.hover(within(band).getByText(/输入 23\.4k/));
    expect((await screen.findAllByText(/输入 23,400（含缓存 18,000）/))[0]).toBeTruthy();
    expect(screen.getAllByText(/单次执行/)[0]).toBeTruthy();
    cleanup();
    const unknown = workflow();
    unknown.turns = [{ ...unknown.turns[0]!, tokenUsage: null }, { ...unknown.turns[1]!, tokenUsage: { inputTokens: null, cachedInputTokens: null, outputTokens: 4200, source: "zcode-native", scope: "attempt" } }];
    open({ step: 3, replies: { workflow: unknown } });
    await ready();
    const partial = pane().querySelector("[data-slot=detail-band]")!.textContent!;
    expect(partial).toContain("输入 未知");
    expect(partial).toContain("输出 4.2k");
    expect(partial).not.toMatch(/输入 0\b/);
    cleanup();
    open({ step: 7, replies: { workflow: { ...unknown, currentTurn: { ...unknown.currentTurn!, tokenUsage: null } } } });
    await ready();
    expect(pane().querySelector("[data-slot=detail-band]")!.textContent).toMatch(/输入 未知.*输出 未知.*缓存 未知/);
  });

  it("writes the session short, copyable, with whether it was resumed, and where it is stored in the hint", async () => {
    const sessioned = task({ selectedAttempt: { attemptId: "att-2", result: { terminationReason: "completed", result: { nativeSession: {
      storageScope: "task-private", storageOwner: "hey-my-buddy-attempt", nativeAppVisibility: "not-listed-in-native-app" } } } } });
    open({ step: 7, replies: { task: sessioned } });
    await ready();
    const band = pane().querySelector("[data-slot=detail-band]") as HTMLElement;
    expect(band.textContent).toContain("恢复");
    const copy = within(band).getByRole("button", { name: /复制标识 01a2f7c4-2e31/ });
    expect(copy.textContent).toContain("…");
    await userEvent.setup().hover(copy);
    expect((await screen.findAllByText(/Buddy 私有存储 · 由 Hey my buddy 的执行持有 · 原生应用里看不到/))[0]).toBeTruthy();
    cleanup();
    open({ step: 3 });
    await ready();
    expect(pane().querySelector("[data-slot=detail-band]")!.textContent).toContain("新会话");
    await userEvent.setup().hover(within(pane()).getByRole("button", { name: /复制标识/ }));
    expect((await screen.findAllByText(/存储位置未记录 · 是否可见未记录/))[0]).toBeTruthy();
  });

  it("writes why an execution did not end as it should: the cause, who cancelled it and why, a native limit", async () => {
    const stopped = task({ status: "cancelled", workflow: { ...task().workflow, state: "cancelled" }, stopInitiator: "host",
      selectedAttempt: { attemptId: "att-2", result: { terminationReason: "wrap-up" }, stopInitiator: "host" },
      cancellation: { actor: HOST, note: "改由 Host 接手" } });
    open({ step: 7, replies: { task: stopped, workflow: workflow({ state: "cancelled" }) } });
    await ready();
    expect(pane().textContent).toContain("终止原因：Host 收尾完成");
    expect(pane().textContent).toContain("Host 停止");
    expect(pane().textContent).toContain("改由 Host 接手");
    cleanup();
    const limited = task({ status: "failed", workflow: { ...task().workflow, state: "failed" }, quotaFailure: { code: "quota-exceeded" }, selectedAttempt: { attemptId: "att-2", result: { terminationReason: "harness-error" } } });
    open({ step: 7, replies: { task: limited, workflow: workflow({ state: "failed" }) } });
    await ready();
    expect(pane().textContent).toContain("额度耗尽");
    cleanup();
    // A turn that ended as it should says nothing of its ending.
    open({ step: 7 });
    await ready();
    expect(pane().textContent).not.toContain("终止原因");
  });

  it("shows no node for the session's reasoning or tool calls, and no placeholder for them", async () => {
    open({ step: 3 });
    await ready();
    expect(pane().textContent).not.toMatch(/推理|读取了|工具调用|会话内容/);
    expect(pane().querySelectorAll("[data-slot=detail-thread] > div")).toHaveLength(2);
  });
});

describe("a turn that is running", () => {
  function live(reminders = { overdue: true, possiblyStuck: true, lastNativeEventAt: null, toolInProgress: false }, activity: unknown = { phase: "tool-running", observedAt: at("11:11"), toolName: "Bash", lastToolActivityAt: at("11:11"), counts: { modelTurns: 7, toolCalls: 23 } }) {
    const running = timeline();
    Object.assign(running.spans.find(item => item.spanId === "e2")!, { endAt: null, state: "executing", shutdownConfirmed: false });
    const record = task({ status: "running", activeAttemptId: "att-2", selectedAttempt: { attemptId: "att-2", reminders }, activity });
    const current = workflow({ state: "executing" });
    return open({ step: 7, timeline: running, replies: { task: record, workflow: current, inquiries: { items: [inquiry({ attemptId: "att-2" })], more: false } } });
  }

  it("keeps a row at the bottom with the phase it is in and when it last said so, and counts the model turns and tool calls", async () => {
    live();
    await ready();
    const row = pane().querySelector("[data-slot=detail-live]") as HTMLElement;
    expect(row.textContent).toContain("工具执行中");
    expect(row.textContent).toContain("Bash");
    expect(row.textContent).toMatch(/上报 .*前/);
    expect(pane().querySelector("[data-slot=detail-band]")!.textContent).toContain("模型回合 7");
    expect(pane().querySelector("[data-slot=detail-band]")!.textContent).toContain("工具调用 23");
    expect(pane().textContent).toContain("已送达，等待答复");
    // A turn that is not finished has no result yet.
    expect(pane().textContent).not.toContain("第二轮：详情页已经按回合重做。");
  });

  it("calls a report of the phase alone unknown progress, and never an estimate of what is done", async () => {
    live({ overdue: false, possiblyStuck: false, lastNativeEventAt: null, toolInProgress: false }, { phase: "waiting-model", observedAt: at("11:29") });
    await ready();
    expect(pane().querySelector("[data-slot=detail-live]")!.textContent).toContain("进展未知");
    expect(pane().textContent).not.toMatch(/\d+\s*%/);
  });
});

describe("a Host's decision", () => {
  it("is named for the request it answers, with what was asked and what was decided, by whom and when", async () => {
    open({ step: 4 });
    await ready();
    expect(within(pane()).getByRole("heading", { name: "协助请求" })).toBeTruthy();
    const text = pane().textContent!;
    expect(text).toContain("需要读取样例目录");
    expect(text).toContain("已读完工作树");
    expect(text).toContain("批准读取样例目录");
    expect(text).toContain("读取后继续");
    expect(text).toContain("批准");
    expect(text).toContain("codex-desktop");
    expect(text).toContain("可以读取");
    // What the blackboard says of why it waits is not shown.
    expect(text).not.toMatch(/the current turn|awaiting/i);
  });

  it("names a request still open, with another count when several wait, and offers no way to decide", async () => {
    const open2 = { requestId: "req-2", kind: "assistance", state: "open", createdAt: at("11:00"), decidedAt: null, childTaskId: "child-1", routing: false,
      summary: "要不要扩大范围", reason: null, attempted: "已试过窄范围", neededWork: ["选一个范围"], expectedArtifacts: ["docs/x.md"], acceptance: "范围确定", preparationError: { code: "WORKSPACE_INVALID", message: "检出准备失败" } };
    const live = timeline();
    live.spans = live.spans.filter(item => !["e2", "q2"].includes(item.spanId));
    live.spans.push(span("h2", "host", at("11:00"), null, { state: "open", requestId: "req-2", requestKind: "assistance" }));
    live.events = live.events.filter(item => item.kind === "dispatch");
    live.rows = [row({ state: "awaiting-host", category: "host" })];
    const f = open({ timeline: live, replies: { workflow: workflow({ state: "awaiting-host", activeRequest: open2 as never, counts: { turns: 2, openRequests: 3 } }) } });
    await ready();
    const text = pane().textContent!;
    expect(text).toContain("要不要扩大范围");
    expect(text).toContain("另有 2 项待决定");
    expect(text).toContain("WORKSPACE_INVALID");
    expect(text).toContain("检出准备失败");
    expect(text).toContain("docs/x.md");
    expect(within(pane()).getAllByText("等待 Host").length).toBeGreaterThan(0);
    expect(pane().querySelectorAll("input, textarea, select, form")).toHaveLength(0);
    await f.user.click(within(pane()).getByRole("button", { name: "打开协助任务" }));
    expect(f.onOpenRun).toHaveBeenCalledWith("child-1");
  });

  it("is a routing boundary for a request about routing: read, and nothing to fill in or to try again", async () => {
    const boundary = { requestId: "req-3", kind: "assistance", state: "open", createdAt: at("11:00"), decidedAt: null, childTaskId: null, routing: true,
      summary: "路由需要 Host 指定 buddy", reason: null, attempted: null, neededWork: [], expectedArtifacts: [], acceptance: null, preparationError: null };
    const live = timeline();
    live.spans = live.spans.filter(item => !["e2", "q2"].includes(item.spanId));
    live.spans.push(span("h3", "host", at("11:00"), null, { state: "open", requestId: "req-3", requestKind: "assistance" }));
    live.events = live.events.filter(item => item.kind === "dispatch");
    open({ timeline: live, replies: { workflow: workflow({ state: "awaiting-host", activeRequest: boundary as never, counts: { turns: 2, openRequests: 1 } }) } });
    await ready();
    expect(within(pane()).getByRole("heading", { name: "路由边界" })).toBeTruthy();
    expect(pane().textContent).toContain("路由需要 Host 指定 buddy");
    expect(within(pane()).queryByRole("button", { name: /重试|填入|使用此 buddy|接续/ })).toBeNull();
    expect(within(pane()).queryByLabelText(/buddy/)).toBeNull();
  });

  it("names a continuation for what it is, with who gave it and why", async () => {
    open({ step: 5 });
    await ready();
    expect(within(pane()).getByRole("heading", { name: "继续" })).toBeTruthy();
    expect(pane().textContent).toContain("批准后继续");
    expect(pane().textContent).toContain("codex-desktop");
  });

  it("is told apart from a handoff, a takeover and a cancellation, which each have their own step", async () => {
    const changed = timeline();
    changed.events.push(event(4, "takeover", at("10:50")), event(5, "cancel", at("10:55"), { actor: HOST, note: "不再需要" }));
    const f = open({ timeline: changed, replies: { task: task({ status: "cancelled", cancellation: { actor: HOST, note: "不再需要" }, selectedAttempt: { result: { terminationReason: "forced-stop" } } }) } });
    await ready();
    // A change of buddy is the boundary between two turns; the takeover and the cancellation are their own marks.
    f.atStep(6);
    expect(pane().textContent).toContain("gpt-6.1-sol");
    expect(pane().textContent).toContain("claude-opus-5-5");
    fireEvent.click(stripSlice("event:4")!);
    await waitFor(() => expect(within(pane()).getByRole("heading", { level: 3, name: "接管" })).toBeTruthy());
    fireEvent.click(stripSlice("event:5")!);
    await waitFor(() => expect(within(pane()).getByRole("heading", { level: 3, name: "Host 停止" })).toBeTruthy());
    expect(within(pane()).getByText("停止发起", { selector: "dt" }).nextElementSibling?.textContent).toBe("Host 停止");
    expect(pane().textContent).toContain("不再需要");
  });
});

describe("the acceptance", () => {
  const settled = () => {
    const found = timeline();
    found.events.push(event(4, "integrate", at("11:03")), event(5, "accept", at("11:05")));
    found.rows = [row({ state: "accepted", acceptedAt: at("11:05"), acceptanceVerdict: "accepted" })];
    return found;
  };
  const artifact: Artifact = { artifactId: "art-final", kind: "output", attemptId: "att-2", manifestSha256: "5f0c81d2" + "0".repeat(52) + "e2a1", outputCommit: "f6dfd59c11",
    diffPath: null, changedPaths: Array.from({ length: 14 }, (_, index) => `f${index}.ts`), partial: false, verified: null, final: null, cumulativePatch: null };
  const verified: Integration = { integrationId: "int-1", artifactId: "art-final", state: "verified", strategy: "merge", target: { path: "~/projects/hey-my-buddy", ref: "socu/console" },
    beforeCommit: "b7285470aa", afterCommit: "f6dfd59c11", verificationSummary: "前端全量通过；构建可复现。", hostPaths: ["apps/console/src/tokens.css", "apps/console/src/button.tsx"], note: null, actor: HOST, createdAt: at("11:03") };

  it("is the final artifact and the evidence that it was integrated, as the Host recorded it", async () => {
    open({ timeline: settled(), step: 8, replies: { workflow: workflow({ state: "accepted", artifacts: [artifact], finalArtifactId: "art-final", integrations: [verified] }), task: task({ acceptedAt: at("11:05"), acceptanceVerdict: "accepted" }) } });
    await ready();
    const text = pane().textContent!;
    expect(within(pane()).getByRole("heading", { name: "验收" })).toBeTruthy();
    expect(text).toContain("已验收");
    expect(text).toContain("f6dfd59c11");
    expect(text).toContain("14");
    expect(text).toContain("5f0c81d2…e2a1");
    expect(text).toContain("已验证");
    expect(text).toContain("merge");
    expect(text).toContain("~/projects/hey-my-buddy @ socu/console");
    expect(text).toContain("b7285470 → f6dfd59c");
    expect(text).toContain("前端全量通过；构建可复现。");
    expect(text).toContain("Host 补充的改动 2");
    expect(text).toContain("apps/console/src/tokens.css");
    expect(text).toContain("codex-desktop");
    // Nothing exceptional: no stop is unconfirmed and the ending was normal.
    expect(text).not.toContain("例外");
  });

  it("writes that an integration was not required with its reason, and that none was recorded as it is", async () => {
    const notRequired: Integration = { ...verified, state: "not-required", strategy: "none", target: null, beforeCommit: null, afterCommit: null, note: "只改了文档", hostPaths: [] };
    open({ timeline: settled(), step: 8, replies: { workflow: workflow({ state: "accepted", artifacts: [artifact], finalArtifactId: "art-final", integrations: [notRequired] }) } });
    await ready();
    expect(pane().textContent).toContain("无需整合");
    expect(pane().textContent).toContain("只改了文档");
    cleanup();
    open({ timeline: settled(), step: 8, replies: { workflow: workflow({ state: "accepted", artifacts: [artifact], finalArtifactId: "art-final", integrations: [] }) } });
    await ready();
    expect(pane().textContent).toContain("整合证明未记录");
    // An integration of another artifact is no evidence for this one.
    cleanup();
    open({ timeline: settled(), step: 8, replies: { workflow: workflow({ state: "accepted", artifacts: [artifact], finalArtifactId: "art-final", integrations: [{ ...verified, artifactId: "art-other" }] }) } });
    await ready();
    expect(pane().textContent).toContain("整合证明未记录");
  });

  it("waits, for a delivered micro task, and says the acceptance is not yet", async () => {
    open({ step: 8 });
    await ready();
    expect(pane().textContent).toContain("等待验收");
    expect(chosenStep()).toContain("验收");
  });

  it("writes the review that sent the delivery back, and does not call it accepted", async () => {
    const sent = timeline();
    sent.events.push(event(4, "reject", at("11:06")));
    sent.rows = [row({ reviewRejectedAt: at("11:06"), reviewRejectionNote: "需要补充验证" })];
    open({ timeline: sent, step: 8, replies: { task: task({ reviewRejectedAt: at("11:06"), reviewRejectionNote: "需要补充验证" }) } });
    await ready();
    expect(pane().textContent).toContain("已打回");
    expect(pane().textContent).toContain("需要补充验证");
    expect(pane().textContent).not.toContain("已验收");
  });

  it("is, for a failed micro task, the Host's conclusion as its own record, which does not count as acceptance", async () => {
    const failed = timeline();
    failed.events.push(event(4, "conclude", at("11:07")));
    failed.rows = [row({ state: "failed", status: "failed" })];
    const conclusion = { conclusionId: "c1", reason: null, executionStatus: "failed", note: "额度耗尽前把改动提取为部分成果", evidence: ["部分成果已封存", "检出内容已保留"],
      artifactId: "art-partial", integrationId: null, attemptId: "att-2", actor: { role: "host" as const, id: "host-a" }, createdAt: at("11:07"), runRevision: 7 };
    open({ timeline: failed, step: 8, replies: { workflow: workflow({ state: "failed", hostConclusion: conclusion }), task: task({ status: "failed", workflow: { ...task().workflow, state: "failed" } }) } });
    await ready();
    const text = pane().textContent!;
    expect(text).toContain("Host 的结论");
    expect(text).toContain("失败 · 不计入验收");
    expect(text).toContain("host-a");
    expect(text).toContain("V7");
    expect(text).toContain("att-2");
    expect(text).toContain("art-partial");
    expect(text).toContain("额度耗尽前把改动提取为部分成果");
    expect(text).toContain("部分成果已封存");
    expect(text).not.toContain("已验收");
    cleanup();
    open({ timeline: failed, step: 8, replies: { workflow: workflow({ state: "cancelled", hostConclusion: { ...conclusion, executionStatus: "cancelled" } }) } });
    await ready();
    expect(pane().textContent).toContain("已取消 · 不计入验收");
  });

  it("writes only exceptions: a stop that is not confirmed, an ending that was not the normal one", async () => {
    const unconfirmed = workflow({ shutdown: { selfConfirmed: true, descendantsConfirmed: false, unconfirmedCount: 1 } });
    open({ step: 8, replies: { workflow: unconfirmed, task: task({ acceptanceVerdict: "accepted" }) } });
    await ready();
    expect(pane().textContent).toContain("例外");
    expect(pane().textContent).toContain("停止未确认，1 项执行尚未核实");
    cleanup();
    open({ step: 8, replies: { task: task({ selectedAttempt: { attemptId: "att-2", result: { terminationReason: "grace-expired" } } }) } });
    await ready();
    expect(pane().textContent).toContain("收尾宽限到期后停止");
  });
});

describe("the detail is only read", () => {
  it("has no field to write in and asks the board only to read", async () => {
    const f = open();
    await ready();
    for (let index = 1; index <= 8; index += 1) {
      f.atStep(index);
      expect(document.querySelectorAll("[data-slot=task-detail] input, [data-slot=task-detail] textarea, [data-slot=task-detail] select, [data-slot=task-detail] form")).toHaveLength(0);
    }
    expect(Object.keys(f.reads).sort()).toEqual(["audit", "basis", "held", "inquiries", "task", "workflow"]);
  });

  it("names a turn's buddy only as it was recorded for the turn, never as the micro task's current one", async () => {
    open({ step: 3, replies: { workflow: workflow({ buddy: OPUS }) } });
    await ready();
    expect(pane().querySelector("[data-slot=detail-band]")!.textContent).toContain(CODEX.model);
    expect(pane().querySelector("[data-slot=detail-band]")!.textContent).not.toContain(OPUS.model);
  });
});

it("opens and highlights the exact inquiry in its turn, then scrolls it into view", async () => {
  const found = timeline();
  const containing = found.spans.find(item => item.attemptId === "att-1" && item.kind === "execution")!;
  containing.inquiries = [{ inquiryId: "inq-1", createdAt: at("10:20"), author: "host", state: "delivered" }];
  found.spans.unshift({ ...containing, spanId: "another-task-question", runId: "another-task" });
  const scroll = vi.fn();
  const original = HTMLElement.prototype.scrollIntoView;
  HTMLElement.prototype.scrollIntoView = scroll;
  try {
    open({ origin: "inquiry:inq-1", timeline: found, replies: { inquiries: { items: [inquiry({ messageId: "different-message-id", inquiryId: "inq-1" })], more: false } } });
    await ready();
    await waitFor(() => expect(document.querySelector('[data-inquiry-id="inq-1"]')?.getAttribute("data-highlighted")).toBe("true"));
    expect(pane().textContent).toContain("回合 1");
    expect(stripSlice("span:e1")!.getAttribute("aria-pressed")).toBe("true");
    expect(scroll).toHaveBeenCalledWith(expect.objectContaining({ block: "center" }));
  } finally { HTMLElement.prototype.scrollIntoView = original; }
});

it("can close the detail while its first read is pending or has failed", async () => {
  const reads = client();
  vi.mocked(reads.task).mockImplementation(() => new Promise(() => {}));
  const pending = open({ client: reads });
  await pending.user.click(screen.getByRole("button", { name: "收起详情" }));
  expect(pending.onBack).toHaveBeenCalledOnce();
  cleanup();
  vi.mocked(reads.task).mockRejectedValue(new Error("missing task"));
  const failed = open({ client: reads });
  await screen.findByRole("heading", { name: "读取微任务失败" });
  await failed.user.click(screen.getByRole("button", { name: "收起详情" }));
  expect(failed.onBack).toHaveBeenCalledOnce();
});
