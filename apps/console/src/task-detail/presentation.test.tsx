import { cleanup, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { at, audit, event, inquiry, row, task, timeline, workflow } from "./task-detail.fixtures";
import { open, pane, ready } from "./task-detail.testing";
import { buildSteps } from "./steps";
import { parseTask } from "./parse";

afterEach(cleanup);

describe("the common step presentation", () => {
  it("puts the title before waits, tabs and content for every kind of step", async () => {
    const recorded = timeline();
    recorded.events.push(event(4, "takeover", at("10:50")), event(5, "cancel", at("10:55")));
    const steps = buildSteps(recorded, "m1", Date.parse(at("11:30"))).steps;
    expect(new Set(steps.map(step => step.kind))).toEqual(new Set(["delegation", "routing", "turn", "decision", "continue", "handoff", "takeover", "cancel", "acceptance"]));
    const f = open({ step: 1, timeline: recorded });
    await ready();
    for (const [index, step] of steps.entries()) {
      f.atStep(index + 1);
      const title = pane().querySelector('[data-slot="pane-title"]')!;
      const content = pane().querySelector('[data-slot="scroll-area-viewport"]')!;
      const tabs = within(pane()).queryByRole("tablist");
      expect(Boolean(tabs), step.kind).toBe(step.kind === "routing" || step.kind === "turn");
      expect(title.compareDocumentPosition(content) & Node.DOCUMENT_POSITION_FOLLOWING, step.kind).toBeTruthy();
      for (const wait of step.waits) {
        const line = pane().querySelector(`[data-item-key="${wait.spanId === null ? "settle:m1" : `span:${wait.spanId}`}" ]`)!;
        expect(line).toBeTruthy();
        expect(title.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING, step.kind).toBeTruthy();
        expect(line.compareDocumentPosition(tabs ?? content) & Node.DOCUMENT_POSITION_FOLLOWING, step.kind).toBeTruthy();
      }
      if (tabs) expect(tabs.compareDocumentPosition(content) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("omits repeated delegation provenance and ID, but records changed ownership", async () => {
    const f = open({ step: 1 });
    await ready();
    expect(pane().textContent).not.toMatch(/微任务 ID|原始委派方|当前负责方/);
    expect(pane().querySelector('[data-state][aria-describedby]')).toBeNull();
    f.reads.held.task.lineage!.currentHostId = "replacement-host";
    await f.user.click(document.querySelector('[data-slot=detail-header] button[aria-label="重新读取"]')!);
    expect(await within(pane()).findByText("当前负责方")).toBeTruthy();
    expect(pane().textContent).toContain("replacement-host");
    expect(pane().textContent).not.toContain("原始委派方");
  });

  it("uses a neutral request quotation, a single title attribution and the Host's Markdown response", async () => {
    const history = audit();
    history.requests[0]!.summary = "## 需要读取\n\n- 核对 `sample/`";
    history.requests[0]!.decision!.note = "**允许**读取 `sample/`";
    open({ step: 4, replies: { audit: history } });
    await ready();
    const quote = pane().querySelector("blockquote")!;
    expect(within(quote).getByText("协助请求").className).toContain("text-ink3");
    expect(quote.className).toContain("border-l-2");
    expect(quote.textContent).toContain("已尝试");
    expect(quote.querySelector("h2")?.textContent).toBe("需要读取");
    const response = pane().querySelector('[data-slot="host-response"]')!;
    expect(response.querySelector("[data-streamdown=strong]")?.textContent).toBe("允许");
    expect(quote.compareDocumentPosition(response) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(pane()).getByRole("heading", { name: "决定" })).toBeTruthy();
    expect(pane().querySelector('[data-slot="pane-title"]')?.textContent).toContain("批准");
    expect(pane().querySelectorAll("dt").length).toBeGreaterThan(0);
    expect([...pane().querySelectorAll("dt")].map(node => node.textContent)).not.toEqual(expect.arrayContaining(["谁", "决定", "时刻"]));
    expect(pane().textContent!.match(/Host · codex-desktop/g)).toHaveLength(1);
    expect(pane().querySelector(".text-hand")).toBeNull();
  });

  it("points a returned request to the next step and leaves its body there", async () => {
    const history = audit();
    history.turns[0]!.disposition = "assistance";
    history.turns[0]!.summary = "需要读取样例目录";
    open({ step: 3, replies: { audit: history } });
    await ready();
    expect(pane().textContent).toContain("已向 Host 提交请求，见下一步。");
    expect(pane().textContent).not.toContain("需要读取样例目录");
    expect(pane().textContent).not.toContain(task().task);
  });

  it("renders the actual continuation input in its step, and not inside the later turn", async () => {
    const f = open({ step: 5 });
    await ready();
    expect(pane().textContent).toContain(audit().continuations[0]!.input);
    expect(pane().querySelector("[data-slot=pane-title] time")?.getAttribute("datetime")).toBe(new Date(at("10:41")).toISOString());
    expect(pane().querySelector("[data-slot=host-response]")?.textContent).not.toContain("可以读取");
    f.atStep(7);
    expect(pane().textContent).not.toContain(audit().continuations[0]!.input);
    expect(pane().textContent).not.toContain(task().task);
  });

  it("parses and displays the acceptance note as Markdown, with a single accepted state", async () => {
    const accepted = task({ acceptedAt: at("11:05"), acceptanceNote: "## 验收说明\n\n- 已核对 `layout.tsx`" });
    expect(parseTask(accepted, "m1").acceptanceNote).toContain("验收说明");
    expect(() => parseTask({ ...accepted, acceptanceNote: 17 }, "m1")).toThrow();
    const recorded = timeline({ rows: [row({ state: "accepted", acceptedAt: at("11:05") })] });
    recorded.events.push(event(4, "accept", at("11:05")));
    open({ step: 8, timeline: recorded, replies: { task: accepted, workflow: workflow({ state: "accepted" }) } });
    await ready();
    expect(within(pane()).getByRole("heading", { name: "验收说明" })).toBeTruthy();
    expect(pane().querySelector("li code")?.textContent).toBe("layout.tsx");
    expect(pane().textContent!.match(/已验收/g)).toHaveLength(1);
  });

  it("quotes a Host inquiry and puts its Markdown answer beneath it without question/answer labels", async () => {
    open({ step: 3, replies: { inquiries: { more: false, items: [inquiry({ question: "核对 **当前**进展", state: "answered", answer: { at: at("10:22"), text: "- 完成 `parse.ts`", truncated: false } })] } } });
    await ready();
    const item = pane().querySelector('[data-item-key="inquiry:inq-1"]')!;
    expect(item.querySelector("blockquote [data-streamdown=strong]")?.textContent).toBe("当前");
    expect(item.querySelector('[data-slot="host-response"] li code')?.textContent).toBe("parse.ts");
    expect(item.querySelector('[data-slot="request-kind"]')?.className).toContain("text-ink3");
    expect(item.querySelector('[data-slot="tooltip-trigger"]')).toBeNull();
    expect(item.textContent).not.toMatch(/问：|答：|谁/);
  });
});
