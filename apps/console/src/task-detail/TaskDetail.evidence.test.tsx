import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CODEX, HOST, NO_BASIS, OPUS, REASON, at, audit, client, event, inquiry, result, routing, row, span, task, timeline, workflow } from "./task-detail.fixtures";
import { chosenStep, open, pane, ready, strip, stripSlice } from "./task-detail.testing";
import type { Artifact, Basis, Integration } from "./types";

afterEach(cleanup);

const band = () => pane().querySelector("[data-slot=detail-band]") as HTMLElement;
const head = () => document.querySelector("[data-slot=detail-header]") as HTMLElement;
const turnTitle = () => within(pane()).getByRole("heading", { name: /^回合/ }).parentElement!;
const slices = () => [...strip().querySelectorAll<SVGElement>("[data-key]")];

/** `e2` ran and did not end: the stop is not confirmed. */
function unsure() {
  const found = timeline();
  Object.assign(found.spans.find(item => item.spanId === "e2")!, { uncertain: true, shutdownConfirmed: false });
  return found;
}

describe("a stop that is not confirmed", () => {
  it("is written as not confirmed in the turn, in the route and on the strip, never as ended", async () => {
    open({ step: 7, timeline: unsure() });
    await ready();
    expect(within(pane()).getByRole("heading", { name: "回合 2" }).parentElement!.textContent).toContain("结束未确认");
    expect(pane().textContent).not.toContain("已结束");
    expect(stripSlice("span:e2")!.getAttribute("aria-pressed")).toBe("true");
    expect(stripSlice("span:e2")!.getAttribute("aria-label")).toContain("停止未确认");
    cleanup();
    const route = timeline();
    Object.assign(route.spans.find(item => item.spanId === "r1")!, { uncertain: true });
    open({ step: 2, timeline: route });
    await ready();
    expect(pane().textContent).toContain("结束未确认");
    expect(pane().textContent).not.toContain("已结束");
  });

  it("does not need the acceptance to be there to be said", async () => {
    const found = unsure();
    found.rows = [row({ state: "executing", category: "active", shutdownConfirmed: false })];
    found.events = found.events.filter(item => item.kind === "dispatch" || item.kind === "continue");
    open({ step: 7, timeline: found, replies: { workflow: workflow({ state: "executing" }) } });
    await ready();
    expect(strip().querySelector('[data-key="acceptance-wait"]')).toBeNull();
    expect(pane().textContent).toContain("结束未确认");
  });
});

describe("the integration and the acceptance", () => {
  const output: Artifact = { artifactId: "art-final", kind: "output", attemptId: "att-2", manifestSha256: "5f0c81d2" + "0".repeat(52) + "e2a1", outputCommit: "f6dfd59c11",
    diffPath: null, changedPaths: ["a.ts"], partial: false, verified: null, final: null, cumulativePatch: null };
  const verified: Integration = { integrationId: "int-1", artifactId: "art-final", state: "verified", strategy: "merge", target: { path: "~/p", ref: "main" },
    beforeCommit: "b7285470aa", afterCommit: "f6dfd59c11", hostPaths: ["new-integration.ts"], note: null, actor: HOST, createdAt: at("11:03") };
  const integrated = () => {
    const found = timeline();
    found.events.push(event(4, "integrate", at("11:03")));
    return found;
  };

  it("keeps waiting for the acceptance when only the integration is recorded, and lists the evidence of the integration", async () => {
    open({ step: 8, timeline: integrated(), replies: { workflow: workflow({ artifacts: [output], finalArtifactId: "art-final", integrations: [verified] }) } });
    await ready();
    expect(pane().textContent).toContain("等待验收");
    expect(pane().textContent).not.toContain("已结束");
    expect(pane().textContent).toContain("new-integration.ts");
    expect(strip().querySelector('[data-key="acceptance-wait"]')!.getAttribute("aria-label")).toContain("等待验收");
    expect(head().textContent).toContain("等待验收");
  });

  it("takes the latest integration of the final artifact, and a partial output is never the final one", async () => {
    const older: Integration = { ...verified, integrationId: "int-old", afterCommit: "0000000000", hostPaths: ["old-integration.ts"] };
    const resolved: Artifact = { ...output, artifactId: "art-resolved", kind: "resolved-output" };
    const partial: Artifact = { ...output, artifactId: "art-partial", kind: "partial-output", partial: true, verified: false, final: false };
    open({ step: 8, timeline: integrated(), replies: { workflow: workflow({ artifacts: [partial, resolved, output], finalArtifactId: "art-resolved",
      integrations: [{ ...verified, artifactId: "art-resolved" }, { ...older, artifactId: "art-resolved" }] }) } });
    await ready();
    expect(pane().textContent).toContain("new-integration.ts");
    expect(pane().textContent).not.toContain("old-integration.ts");
    cleanup();
    open({ step: 8, timeline: integrated(), replies: { workflow: workflow({ artifacts: [partial], finalArtifactId: "art-partial", integrations: [] }) } });
    await ready();
    expect(within(pane()).queryByText("产物")).toBeNull();
    cleanup();
    open({ step: 8, timeline: integrated(), replies: { workflow: workflow({ artifacts: [output], finalArtifactId: null, integrations: [verified] }) } });
    await ready();
    expect(within(pane()).queryByText("产物")).toBeNull();
  });
});

describe("a route", () => {
  const two = () => {
    const found = timeline();
    found.spans.find(item => item.spanId === "r1")!.routing = { routingMode: "review", selectedBuddy: OPUS, reason: null, note: "审阅模式不可用之前的结论", usage: { elapsedMs: 9000, toolCalls: 4 } };
    found.spans.push(span("r2", "routing", at("10:40"), at("10:41"), { attemptId: "att-r2", decisionId: "decision-1",
      routing: { routingMode: "fast", selectedBuddy: CODEX, reason: null, note: "改用快速模式后的结论", usage: { elapsedMs: 3000, toolCalls: 0 } } }));
    return found;
  };

  it("keeps each route's own facts when one decision was routed twice: the history adds only the Router and the source", async () => {
    open({ step: 2, timeline: two() });
    await ready();
    expect(pane().textContent).toContain("审阅模式不可用之前的结论");
    expect(pane().textContent).not.toContain("改用快速模式后的结论");
    expect(band().textContent).toContain("审阅");
    expect(band().textContent).toContain("gpt-6-luna");
    fireEvent.click(stripSlice("span:r2")!);
    await waitFor(() => expect(pane().textContent).toContain("改用快速模式后的结论"));
    expect(pane().textContent).not.toContain("审阅模式不可用之前的结论");
    expect(band().textContent).toContain("快速");
    expect(band().textContent).toContain("工具调用 0");
  });

  it("writes what a route's own record lacks as unrecorded, and takes it from nowhere else", async () => {
    const found = timeline();
    found.spans.find(item => item.spanId === "r1")!.routing = null;
    open({ step: 2, timeline: found });
    await ready();
    expect(pane().textContent).not.toContain(REASON);
    expect(band().textContent).toContain("未记录");
    expect(pane().textContent).not.toContain("gpt-6.1-sol");
  });

  it("names the Router of a route on the band, by the decision's record", async () => {
    open({ step: 2, replies: { routing: [routing({ routerBuddy: { harness: "codex", provider: "openai", model: "gpt-6-luna", effort: "max" } })] } });
    await ready();
    expect(band().textContent).toContain("gpt-6-luna / max");
  });

  it("says the program chose when it did, shows the bounds it chose within, and names no Router and no mode", async () => {
    const bounds: Basis = { constraints: { harness: "dsh" }, requiredCapabilities: ["execution:dsh"], candidateCount: 0,
      excluded: [{ buddy: { harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "off" }, note: "提交时排除低推理强度" }], coolingCount: null, cooling: [], preferences: [] };
    const f = open({ step: 2, replies: { routing: [routing({ source: "no-candidate", routerBuddy: null, routingMode: null })], bases: { "decision-1": bounds } } });
    await ready();
    await waitFor(() => expect(pane().textContent).toContain("没有合法候选"));
    expect(pane().textContent).not.toContain("模型选择");
    expect(band().textContent).toContain("未调用 Router");
    await waitFor(() => expect(f.reads.basis).toHaveBeenCalledExactlyOnceWith("decision-1"));
    await f.user.click(await within(pane()).findByRole("button", { name: "提交时的约束" }));
    await waitFor(() => expect(pane().textContent).toContain("硬约束：dsh"));
    expect(pane().textContent).toContain("所需能力：execution:dsh");
    expect(pane().textContent).toMatch(/用户排除 1 个：dsh \/ deepseek-official \/ deepseek-flash \/ off \(提交时排除低推理强度\)/);
    expect(pane().textContent).toContain("提交时冻结候选 0 个");
  });

  it("shows the bounds beside the only candidate, in a hint on the word that says so, with the preference the program applied", async () => {
    const bounds: Basis = { ...NO_BASIS, constraints: { harness: "dsh" }, requiredCapabilities: ["execution:dsh"], candidateCount: 1,
      excluded: [{ buddy: { model: "m" }, note: "提交时排除低推理强度" }], preferences: [{ buddyId: "dsh:p:m:max", mode: "pin", note: "当时固定此 buddy" }] };
    const f = open({ step: 3, replies: { routing: [routing({ source: "single-candidate" })], bases: { "decision-1": bounds } } });
    await ready();
    await waitFor(() => expect(f.reads.basis).toHaveBeenCalledWith("decision-1"));
    await waitFor(() => expect(within(band()).getByText("唯一候选").getAttribute("tabindex")).toBe("0"));
    await f.user.hover(within(band()).getByText("唯一候选"));
    const tip = (await screen.findAllByText(/硬约束：dsh/))[0]!.closest("ul")!.textContent!;
    expect(tip).toContain("所需能力：execution:dsh");
    expect(tip).toMatch(/用户排除 1 个：m \(提交时排除低推理强度\)/);
    expect(tip).toContain("固定选择 dsh:p:m:max：当时固定此 buddy");
  });

  it("reads the bounds only of the decisions the program made, once", async () => {
    const f = open({ step: 2 });
    await ready();
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(f.reads.basis).not.toHaveBeenCalled();
  });
});

describe("the turns and what the compact record leaves out", () => {
  it("counts the turns by the record's count, not by the ones it lists", async () => {
    open({ replies: { workflow: workflow({ counts: { turns: 12, openRequests: 0 } }) } });
    await ready();
    expect(head().textContent).toContain("回合12");
  });

  it("takes a turn the compact record no longer lists from the audit: its session, usage, expected time and route", async () => {
    const early = { ...result(1, "第一轮：读完了旧的详情，列出了要删的部分。"), sessionId: "early-session-0001", expectedSeconds: 900,
      tokenUsage: { inputTokens: 100, cachedInputTokens: 40, outputTokens: 7, source: "codex-native", scope: "attempt" as const },
      routing: { decisionId: null, buddyRevision: 1 } };
    const compact = workflow();
    compact.turns = [compact.turns[0]!];
    open({ step: 3, replies: { workflow: compact, audit: audit({ turns: [early, result(2, "第二轮")] }) } });
    await ready();
    await waitFor(() => expect(band().textContent).toContain("Host 指定"));
    expect(band().textContent).toContain("输入 100");
    expect(band().textContent).toMatch(/会话.*early-session-0001/);
    expect(pane().textContent).toContain("预期 15 分");
    expect(pane().textContent).toContain("第一轮：读完了旧的详情");
  });

  it("writes the expected time of a turn that has ended too", async () => {
    open({ step: 3 });
    await ready();
    expect(pane().textContent).toContain("预期 30 分");
  });
});

describe("a Host wait that ends in a continuation", () => {
  it("shows the request it was for, though the continuation names none", async () => {
    const found = timeline();
    found.spans.find(item => item.spanId === "h1")!.endAt = at("10:41");
    found.events = [event(1, "dispatch", at("10:00")), event(3, "continue", at("10:41"), { continuationId: "cont-1" })];
    open({ timeline: found, step: 4 });
    await ready();
    expect(within(pane()).getByRole("heading", { name: "继续" })).toBeTruthy();
    expect(pane().textContent).toContain("需要读取样例目录");
    expect(pane().textContent).toContain("批准读取样例目录");
  });

  it("gives each of two requests answered at the same instant its own", async () => {
    const found = timeline();
    found.spans = [found.spans[0]!, found.spans[1]!, found.spans[2]!,
      span("hA", "host", at("10:30"), at("10:40"), { state: "approved", requestId: "req-A" }),
      span("hB", "host", at("10:31"), at("10:40"), { state: "approved", requestId: "req-B" }),
      found.spans[5]!];
    found.events = [event(1, "dispatch", at("10:00")), event(2, "continue", at("10:40"), { continuationId: "c-A" }), event(3, "continue", at("10:40"), { continuationId: "c-B" })];
    const full = (id: string, summary: string) => ({ requestId: id, kind: "assistance", state: "approved", createdAt: at("10:30"), decidedAt: at("10:40"), childTaskId: null,
      routing: false, summary, reason: null, attempted: null, neededWork: [], expectedArtifacts: [], acceptance: null, preparationError: null, turnId: "turn-1", decisionId: null, decision: null });
    const audited = audit({ requests: [full("req-A", "第一个请求"), full("req-B", "第二个请求")] });
    const f = open({ timeline: found, replies: { audit: audited } });
    await ready();
    // The two continuations happened at one instant: the strip shows them as one cluster, and each keeps its own step.
    f.atStep(4);
    await waitFor(() => expect(pane().textContent).toContain("第一个请求"));
    expect(pane().textContent).not.toContain("第二个请求");
    f.atStep(5);
    await waitFor(() => expect(pane().textContent).toContain("第二个请求"));
    expect(pane().textContent).not.toContain("第一个请求");
  });
});

describe("the inquiries", () => {
  const items = (...list: ReturnType<typeof inquiry>[]) => ({ items: list, more: false });

  it("shows an inquiry that names no attempt in the turn that was executing when it was put", async () => {
    open({ step: 3, replies: { inquiries: items(inquiry({ messageId: "x", attemptId: null, question: "执行中的问询", createdAt: at("10:20") })) } });
    await ready();
    expect(pane().textContent).toContain("执行中的问询");
  });

  it("shows one put between two turns in the next turn, and one put before the first in the first", async () => {
    open({ step: 7, replies: { inquiries: items(inquiry({ messageId: "x", attemptId: null, question: "两轮之间的问询", createdAt: at("10:41", 30) })) } });
    await ready();
    expect(pane().textContent).toContain("两轮之间的问询");
    cleanup();
    open({ step: 3, replies: { inquiries: items(inquiry({ messageId: "y", attemptId: null, question: "第一轮之前的问询", createdAt: at("09:00") })) } });
    await ready();
    expect(pane().textContent).toContain("第一轮之前的问询");
    cleanup();
    open({ step: 3, replies: { inquiries: items(inquiry({ messageId: "x", attemptId: null, question: "两轮之间的问询", createdAt: at("10:41", 30) })) } });
    await ready();
    expect(pane().textContent).not.toContain("两轮之间的问询");
  });

  it("shows one that no turn can take in the delegation, so that none is lost", async () => {
    const f = open({ step: 1, replies: { inquiries: items(inquiry({ messageId: "x", attemptId: null, question: "没有回合时的问询", createdAt: at("11:20") }),
      inquiry({ messageId: "z", attemptId: "att-gone", question: "回合不在时间轴上的问询", createdAt: at("12:00") })) } });
    await ready();
    expect(f.reads.inquiries).toHaveBeenCalled();
    expect(within(pane()).getByText("没有回合时的问询")).toBeTruthy();
    expect(within(pane()).getByText("回合不在时间轴上的问询")).toBeTruthy();
    cleanup();
    // With no timeline row there is no turn at all: the delegation holds every inquiry.
    const cut = timeline();
    cut.rows = [];
    open({ step: 1, timeline: cut, replies: { inquiries: items(inquiry({ messageId: "x", question: "无行时的问询" })) } });
    await ready();
    expect(within(pane()).getByText("无行时的问询")).toBeTruthy();
  });

  it("says earlier inquiries may not be listed in every turn that began before the earliest one listed, and in no other", async () => {
    const cut = { items: [inquiry({ messageId: "x", attemptId: "att-1", question: "最早列出的问询", createdAt: at("10:20") })], more: true };
    open({ step: 3, replies: { inquiries: cut } });
    await ready();
    expect(pane().textContent).toContain("更早的问询可能没有列出");
    cleanup();
    open({ step: 7, replies: { inquiries: cut } });
    await ready();
    expect(pane().textContent).not.toContain("更早的问询可能没有列出");
  });
});

describe("the head", () => {
  it("keeps the close button with the title and the state beside the refresh control", async () => {
    const f = open();
    await ready();
    const row1 = head().firstElementChild as HTMLElement;
    const children = [...row1.children];
    const titled = children[0] as HTMLElement;
    expect(titled.querySelector("h2")!.textContent).toBe("微任务详情");
    expect(titled.firstElementChild!.getAttribute("aria-label")).toBe("收起详情");
    await f.user.click(titled.firstElementChild!);
    expect(f.onBack).toHaveBeenCalledOnce();
    expect(titled.textContent).not.toContain("等待验收");
    const right = children[1] as HTMLElement;
    expect(right.textContent).toMatch(/开始.*历时.*回合/);
    expect(within(right).getByText("等待验收")).toBeTruthy();
    expect(right.querySelector("[data-slot=detail-status]")?.parentElement?.querySelector("button[aria-label=重新读取]")).toBeTruthy();
    expect(within(right).getByRole("button", { name: "重新读取" })).toBeTruthy();
    expect(children).toHaveLength(2);
  });

  it("shows a read under way, and lets a second press change nothing", async () => {
    const reads = client();
    let release!: () => void;
    const slow = reads.task as ReturnType<typeof vi.fn>;
    const f = open({ client: reads });
    await ready();
    const button = screen.getByRole("button", { name: "重新读取" });
    const before = slow.mock.calls.length;
    slow.mockImplementationOnce(() => new Promise(resolve => { release = () => resolve({ value: undefined, verifiedAtMs: null }); }));
    await f.user.click(button);
    await waitFor(() => expect(button.getAttribute("aria-busy")).toBe("true"));
    await f.user.click(button);
    expect(slow.mock.calls.length).toBe(before + 1);
    release();
  });

  it("says the checking time of the read the head rests on, and none when that read carries none", async () => {
    const reads = client();
    const f = open({ client: reads });
    await ready();
    await f.user.hover(screen.getByRole("button", { name: "重新读取" }));
    expect((await screen.findAllByText(/核对时间 \d{2}:\d{2}:\d{2}/))[0]).toBeTruthy();
    cleanup();
    const bare = client();
    (bare.workflow as ReturnType<typeof vi.fn>).mockImplementation(async () => ({ value: workflow(), verifiedAtMs: null }));
    const g = open({ client: bare });
    await ready();
    await g.user.hover(screen.getByRole("button", { name: "重新读取" }));
    expect((await screen.findAllByText("核对时间未记录"))[0]).toBeTruthy();
  });

  it("writes a relative time with a space between the number and the unit", async () => {
    const running = timeline();
    Object.assign(running.spans.find(item => item.spanId === "e2")!, { endAt: null, state: "executing", shutdownConfirmed: false });
    open({ step: 7, timeline: running, replies: { task: task({ state: "running", activeAttemptId: "att-2", activity: { phase: "tool-running", observedAt: at("11:11"), toolName: "Bash", lastToolActivityAt: at("11:11") } }),
      workflow: workflow({ state: "executing" }) } });
    await ready();
    expect(pane().querySelector("[data-slot=detail-live]")!.textContent).toContain("上报 19 分钟前");
  });
});

describe("the title of a turn that runs", () => {
  function running(reminders: { overdue: boolean; possiblyStuck: boolean }) {
    const live = timeline();
    Object.assign(live.spans.find(item => item.spanId === "e2")!, { endAt: null, state: "executing", shutdownConfirmed: false });
    return open({ timeline: live, replies: { task: task({ state: "running", activeAttemptId: "att-2", selectedAttempt: { reminders: { ...reminders, lastNativeEventAt: null, toolInProgress: false } },
      activity: { phase: "tool-running", observedAt: at("11:11"), toolName: "Bash" } }), workflow: workflow({ state: "executing" }) } });
  }

  it("says possibly stuck in orange where it would say running, and past the expected time when only that is so", async () => {
    running({ overdue: true, possiblyStuck: true });
    await ready();
    expect(turnTitle().textContent).toContain("可能卡住");
    expect(within(turnTitle()).getByText("可能卡住").className).toContain("text-hand");
    cleanup();
    running({ overdue: true, possiblyStuck: false });
    await ready();
    expect(turnTitle().textContent).toContain("已超过预期");
    expect(within(turnTitle()).queryByText("可能卡住")).toBeNull();
    cleanup();
    running({ overdue: false, possiblyStuck: false });
    await ready();
    expect(within(turnTitle()).getByText("进行中").className).toContain("state-breathe");
  });

  it("says what backs the report of a running turn in a hint on its phase", async () => {
    const f = running({ overdue: false, possiblyStuck: false });
    await ready();
    await f.user.hover(within(pane().querySelector("[data-slot=detail-live]") as HTMLElement).getByText("工具执行中"));
    expect((await screen.findAllByText(/收到工具活动/))[0]).toBeTruthy();
  });
});

describe("the helpers of a micro task", () => {
  it("writes the state of each in the fixed words and a mark, not in the board's own", async () => {
    const children = [["h1", "active"], ["h2", "attention"], ["h3", "succeeded"], ["h4", "failed"], ["h5", "cancelled"]] as const;
    const found = timeline();
    found.rows = [row(), ...children.map(([runId]) => row({ runId, title: `协助 ${runId}` }))];
    open({ step: 1, timeline: found, replies: { workflow: workflow({ children: children.map(([taskId, state]) => ({ taskId, role: "helper", state })) }) } });
    await ready();
    const list = within(pane()).getByText("协助 h1").closest("ul")!;
    const words = [...list.children].map(item => item.textContent);
    expect(words).toEqual(["协助 h1进行中", "协助 h2等待 Host", "协助 h3已完成", "协助 h4失败", "协助 h5已结束"]);
    expect(list.textContent).not.toMatch(/active|attention|succeeded|cancelled|delivered/);
    expect(list.querySelectorAll("[data-slot=state-mark]")).toHaveLength(5);
  });
});

describe("the turn that ended otherwise than it should", () => {
  const ended = (record: Record<string, unknown>) => open({ step: 7, replies: { task: task(record), workflow: workflow({ state: "cancelled" }) } });

  it("names each cause in words, and a wrap-up that a Lead asked for as the Lead's", async () => {
    for (const [cause, words] of [["deadline", "Router 预算用完"], ["forced-stop", "强制停止"], ["console-stop", "控制台停止"], ["service-stop", "服务停止"],
      ["unknown-stop", "停止（发起者未知）"], ["harness-error", "Harness 错误"], ["transport-error", "传输故障"], ["grace-expired", "有序收尾的宽限期到期后停止"]] as const) {
      cleanup();
      ended({ state: "failed", selectedAttempt: { result: { terminationReason: cause } } });
      await ready();
      expect(pane().textContent, cause).toContain(words);
    }
    cleanup();
    ended({ state: "cancelled", cancellation: { actor: { role: "lead", id: "review-lead" }, note: "Please summarize the current work" },
      selectedAttempt: { stopInitiator: "lead", result: { terminationReason: "wrap-up" } } });
    await ready();
    expect(pane().textContent).toContain("Lead 有序收尾完成");
    expect(pane().textContent).toContain("Lead 停止");
    expect(pane().textContent).toContain("review-lead");
    expect(pane().textContent).toContain("Please summarize the current work");
    expect(pane().textContent).not.toContain("Host 有序收尾完成");
  });

  it("names the person who stopped it only when the stopper was a person, and the console or the service never by a name", async () => {
    ended({ state: "cancelled", cancellation: { actor: { role: "host", id: "old-actor" }, note: null }, selectedAttempt: { stopInitiator: "console", result: { terminationReason: "console-stop" } } });
    await ready();
    expect(pane().textContent).toContain("控制台停止");
    expect(pane().textContent).not.toContain("old-actor");
    cleanup();
    ended({ state: "cancelled", cancellation: { actor: { role: "host", id: "old-owner" }, note: null }, selectedAttempt: { stopInitiator: "lead", result: { terminationReason: "forced-stop" } } });
    await ready();
    expect(pane().textContent).toContain("Lead 停止");
    expect(pane().textContent).not.toContain("old-owner");
    cleanup();
    ended({ state: "cancelled", selectedAttempt: { result: { terminationReason: "unknown-stop" } } });
    await ready();
    expect(pane().textContent).toContain("停止（发起者未知）");
    expect(pane().textContent).not.toContain("用户取消");
  });

  it("says the cause is not recorded when a run is over and recorded none, and nothing for one that ended as it should", async () => {
    ended({ state: "failed", selectedAttempt: { result: {} } });
    await ready();
    expect(pane().textContent).toContain("终止原因未记录");
    cleanup();
    open({ step: 7 });
    await ready();
    expect(pane().textContent).not.toContain("终止原因");
  });
});

describe("the seal of a turn", () => {
  const sealed = (overrides: Partial<Artifact>): Artifact => ({ artifactId: "art-p", kind: "partial-output", attemptId: "att-1", manifestSha256: "b".repeat(64), outputCommit: "9aa8b7c6d5",
    diffPath: "/d.diff", changedPaths: ["src/a.py"], partial: true, verified: false, final: false, cumulativePatch: null, ...overrides });

  it("says a partial output is neither verified nor final, and gives its hash and its paths", async () => {
    const f = open({ step: 3, replies: { workflow: workflow({ artifacts: [sealed({ cumulativePatch: { baseCommit: "d286db0aa", outputCommit: "9aa8b7c6d5", path: "/p.patch", sha256: "a".repeat(64), changedPaths: ["src/a.py", "src/b.py"] } })] }) } });
    await ready();
    const seal = await within(pane()).findByRole("button", { name: /封存/ });
    expect(seal.textContent).toContain("部分产物");
    await f.user.click(seal);
    await waitFor(() => expect(pane().textContent).toContain("部分、未验证、非最终"));
    expect(pane().textContent).toContain("b".repeat(64));
    expect(pane().textContent).toContain("src/b.py");
  });

  it("writes the parts of a cumulative patch that were not recorded as unrecorded, not as empty", async () => {
    const f = open({ step: 3, replies: { workflow: workflow({ artifacts: [sealed({ kind: "output", partial: false, verified: null, final: null,
      cumulativePatch: { baseCommit: "d286db0aa", outputCommit: null, path: null, sha256: null, changedPaths: [] } })] }) } });
    await f.user.click(await within(await (async () => { await ready(); return pane(); })()).findByRole("button", { name: /封存/ }));
    await waitFor(() => expect(pane().textContent).toContain("累计补丁"));
    expect(pane().textContent!.match(/未记录/g)!.length).toBeGreaterThanOrEqual(3);
  });
});

describe("what a turn says of its session, its usage and its buddy's origin", () => {
  it("says in the hint of the session where it is kept, who holds it, whether the app lists it, whether it can be resumed, whether it is bound, and the harness's own note", async () => {
    const sessioned = task({ selectedAttempt: { result: { terminationReason: "completed", result: { nativeSession: { storageScope: "harness-user-store", storageOwner: "harness-user-store",
      nativeAppVisibility: "listed-in-native-app", resumable: true, bindingPresent: false, note: "Codex owns the native thread" } } } } });
    open({ step: 7, replies: { task: sessioned } });
    await ready();
    await userEvent.setup().hover(within(band()).getByRole("button", { name: /复制标识 01a2f7c4-2e31/ }));
    expect((await screen.findAllByText(/Harness 用户存储 · 由 Harness 的用户目录持有 · 原生应用里列出 · 可恢复 · 私有绑定不存在 · Codex owns the native thread/))[0]).toBeTruthy();
  });

  it("says in the hint of the usage whose it is, where it came from, that it is partial, and that the cache is counted apart when its basis says so", async () => {
    const usage = (extra: object) => ({ inputTokens: 100, cachedInputTokens: 60, outputTokens: 7, source: "codex-native", scope: "attempt" as const, ...extra });
    const withUsage = (tokenUsage: ReturnType<typeof usage>) => { const found = workflow(); found.turns = [found.turns[0]!, { ...found.turns[1]!, tokenUsage }]; return found; };
    const f = open({ step: 3, replies: { workflow: withUsage(usage({ completeness: "partial" })) } });
    await ready();
    await f.user.hover(within(band()).getByText(/输入 100/));
    const tip = (await screen.findAllByText(/来源 codex-native/))[0]!.textContent!;
    expect(tip).toContain("输入 100（含缓存 60）");
    expect(tip).toContain("只含已报告的部分");
    cleanup();
    const apart = open({ step: 3, replies: { workflow: withUsage(usage({ inputBasis: "excludes-cached", inputTokens: 40 })) } });
    await ready();
    await apart.user.hover(within(band()).getByText(/输入 40/));
    expect((await screen.findAllByText(/输入 40（缓存 60 另计）/))[0]!.textContent).toContain("缓存不在输入之内");
  });

  it("shows the bounds beside a buddy the Host chose, for the turn that runs under that choice", async () => {
    const bounds: Basis = { ...NO_BASIS, constraints: { harness: "claude", model: "claude-opus-5-5" }, candidateCount: null };
    const found = workflow();
    found.routing = { status: "explicit", decisionId: null, source: "host-override", basis: bounds };
    open({ step: 7, replies: { workflow: found } });
    await ready();
    await userEvent.setup().hover(within(band()).getByText("Host 指定"));
    expect((await screen.findAllByText(/硬约束：claude \/ claude-opus-5-5/))[0]).toBeTruthy();
  });
});

describe("the delegation", () => {
  it("names a missing project in the console language", async () => {
    open({ step: 1, replies: { task: task({ lineage: { ...task().lineage, project: null } }) } });
    await ready();
    expect(pane().textContent).toContain("未记录项目");
  });

  it("says the delegator is not recorded when it is not, and shows the title, not the result, in the head", async () => {
    open({ step: 1, replies: { task: task({ lineage: { ...task().lineage, sourceHostId: null, currentHostId: null } }) } });
    await ready();
    expect(pane().textContent).toContain("未记录委派方");
    expect(head().querySelector("h2")!.textContent).toBe("微任务详情");
    expect(head().textContent).not.toContain("第二轮：详情页已经按回合重做。");
  });

  it("opens the task as the original, whole when it is short", async () => {
    open({ step: 1 });
    await ready();
    expect(within(pane()).getByText("实现微任务详情，左边是步骤，右边是这一步的内容。")).toBeTruthy();
    expect(within(pane()).queryByRole("button", { name: "展开" })).toBeNull();
  });
});

describe("the detail is only read", () => {
  it("offers none of the controls that were taken out", async () => {
    const removed = ["决定理由", "添加协助任务", "批准所列协助", "拒绝协助并记录理由", "交给下一回合的输入", "提交接续输入", "仍在执行的协助任务", "新的 Host ID", "移交控制权",
      "取消目标及协助任务", "实际检查依据", "接受最终交付", "记录验收问题", "填入已启用 buddy", "使用此 buddy 接续", "重试同一目标的路由"];
    const f = open();
    await ready();
    for (const slice of slices()) {
      fireEvent.click(slice);
      for (const name of removed) {
        expect(screen.queryByLabelText(name), name).toBeNull();
        expect(screen.queryByRole("button", { name }), name).toBeNull();
      }
    }
  });
});
