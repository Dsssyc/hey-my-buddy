import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { afterEach, describe, expect, it } from "vitest";
import { TaskDetail } from "./task-detail/TaskDetail";
import { at, audit, client, task, timeline, workflow } from "./task-detail/task-detail.fixtures";
import type { Replies } from "./task-detail/task-detail.fixtures";

afterEach(cleanup);

/** The detail of `m1`, opened on the delegation step. */
function detail(replies: Replies, step = 1) {
  const reads = client(replies);
  const view = render(<TaskDetail client={reads} runId="m1" timeline={timeline()} timelineLoading={false} nowMs={Date.parse(at("11:30"))}
    step={step} origin={null} active onStep={() => undefined} onBack={() => undefined} onOpenRun={() => undefined} />);
  return { reads, view };
}
const heading = () => document.querySelector("[data-slot=detail-header] h2") as HTMLElement;
const ready = () => waitFor(() => expect(document.querySelector("[data-slot=detail-rail]")).toBeTruthy());

describe("the micro task title rule in the detail (0.16 T1)", () => {
  it.each(["zh", "en"])("shows the original Lead independently of a later owner in %s", async language => {
    await i18n.changeLanguage(language);
    try {
      const lead = task({ lineage: { ...task().lineage!, sourceHostId: null, currentHostId: "host-takeover", rootLeadRunId: "lead-run",
        leadOwnership: { leadRunId: "lead-run", leadTurnId: "lead-turn", createdAt: at("10:00") } } });
      detail({ task: lead, workflow: workflow({ hostId: "host-takeover" }) });
      await ready();
      const original = await screen.findByText(language === "zh" ? "原始委派方" : "Originally delegated by", { selector: "dt" });
      expect(original.nextElementSibling!.textContent).toBe("Lead · lead-run");
      const current = screen.getByText(language === "zh" ? "当前负责方" : "Current owner", { selector: "dt" });
      expect(current.nextElementSibling!.textContent).toBe("host-takeover");
    } finally { await i18n.changeLanguage("zh"); }
  });

  it("shows delegation authority only when the micro task has it", async () => {
    detail({ workflow: workflow({ delegationAuthority: true }) });
    await ready();
    expect(screen.getByText("带委派权")).toBeTruthy();
    cleanup();
    detail({ workflow: workflow({ delegationAuthority: false }) });
    await ready();
    expect(screen.queryByText("带委派权")).toBeNull();
  });

  it("uses the explicit title for the heading; the Worker's summary never becomes the title", async () => {
    const titled = task({ task: "列表行的第一行\n不应出现在标题的第二行", workflow: { ...task().workflow!, title: "修复标题错位" } });
    // The summary is the result of a turn, written as one; the whole task is in the delegation step.
    const turn = detail({ task: titled }, 7);
    await ready();
    expect(heading().textContent).toBe("修复标题错位");
    await waitFor(() => expect(turn.view.container.textContent).toContain("第二轮：详情页已经按回合重做。"));
    expect(heading().textContent).not.toContain("第二轮");
    cleanup();
    const delegation = detail({ task: titled }, 1);
    await ready();
    expect(delegation.view.container.textContent).toContain("不应出现在标题的第二行");
  });

  it("falls back to the first line of the task, and says so, and names a micro task with none", async () => {
    const plain = task({ task: "普通执行任务\n第二行", workflow: { ...task().workflow!, title: null } });
    detail({ task: plain });
    await ready();
    expect(heading().textContent).toBe("普通执行任务");
    await userEvent.setup().hover(heading());
    expect((await screen.findAllByText(/普通执行任务（取自任务首行）/))[0]).toBeTruthy();
    cleanup();
    detail({ task: task({ task: "\n\t ", workflow: { ...task().workflow!, title: null } }) });
    await ready();
    expect(heading().textContent).toBe("未命名微任务");
  });

  it("keeps the explicit title across a refresh of the collaboration record; only the result changes", async () => {
    const reads = client();
    render(<TaskDetail client={reads} runId="m1" timeline={timeline()} timelineLoading={false} nowMs={Date.parse(at("11:30"))}
      step={7} origin={null} active onStep={() => undefined} onBack={() => undefined} onOpenRun={() => undefined} />);
    await ready();
    expect(heading().textContent).toBe("微任务详情");
    await waitFor(() => expect(document.querySelector("[data-slot=detail-pane]")!.textContent).toContain("第二轮：详情页已经按回合重做。"));
    reads.held.workflow = workflow({ revision: 10, currentTurn: { ...workflow().currentTurn!, summary: "历史记录刷新后的最新结论" } });
    reads.held.audit = audit({ revision: 10, turns: [{ turnId: "turn-1", summary: "第一轮", remaining: [] }, { turnId: "turn-2", summary: "历史记录刷新后的最新结论", remaining: [] }] });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 3100)); });
    await waitFor(() => expect(document.querySelector("[data-slot=detail-pane]")!.textContent).toContain("历史记录刷新后的最新结论"));
    expect(heading().textContent).toBe("微任务详情");
  }, 10000);
});
