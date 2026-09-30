import { describe, expect, it } from "vitest";
import type { Task } from "./types";
import { excerpt, taskTitle, titleTooltip } from "./task-state";

const task = (overrides: Partial<Task> = {}): Task => ({
  runId: "run-1", task: "第一行标题\n第二行", status: "queued", owner: "host", cwd: "/repo",
  revision: 1, createdAt: "2026-09-26T00:00:00Z", acceptedAt: null, acceptanceVerdict: null,
  ...overrides,
});

describe("delegation title rule (0.16 T1)", () => {
  it("prefers the explicit workflow title over everything else", () => {
    const title = taskTitle(task({ workflow: { state: "delivered", awaitingHost: false, hostId: "h", ownerGeneration: 1, revision: 3, title: "修复标题", resultSummary: "已验证的整合结果" } }));
    expect(title.text).toBe("修复标题");
    expect(title.source).toBe("title");
  });

  it("never uses the Worker resultSummary as a title", () => {
    const title = taskTitle(task({ workflow: { state: "delivered", awaitingHost: false, hostId: "h", ownerGeneration: 1, revision: 3, resultSummary: "已验证的整合结果" } }));
    expect(title.text).toBe("第一行标题");
    expect(title.source).toBe("task");
  });

  it("falls back to the trimmed first task line without a summary or workflow", () => {
    expect(taskTitle(task()).text).toBe("第一行标题");
    expect(taskTitle(task()).source).toBe("task");
    expect(taskTitle(task({ workflow: { state: "executing", awaitingHost: false, hostId: "h", ownerGeneration: 1, revision: 1, resultSummary: null } })).text)
      .toBe("第一行标题");
  });

  it("caps a task first-line title at roughly 40 characters", () => {
    const long = "长".repeat(120);
    const title = taskTitle(task({ task: long }));
    expect(title.source).toBe("task");
    expect([...title.text]).toHaveLength(41);
    expect(title.text.endsWith("…")).toBe(true);
    // Host-revised 0.16: the shared tooltip keeps the COMPLETE first line.
    expect(title.fullText).toBe(long);
    expect(titleTooltip(title)).toBe(long);
    const explicit = taskTitle(task({ workflow: { state: "delivered", awaitingHost: false, hostId: "h", ownerGeneration: 1, revision: 1, title: "完整显式标题" } }));
    expect(titleTooltip(explicit)).toBe("完整显式标题");
  });

  it("normalizes whitespace in an explicit title to single spaces", () => {
    const title = taskTitle(task({ workflow: { state: "delivered", awaitingHost: false, hostId: "h", ownerGeneration: 1, revision: 2, title: "a\tb  c\r\nd　e" } }));
    expect(title.text).toBe("a b c d e");
  });

  it("names an unnamed delegation only when the title and first line are empty", () => {
    expect(taskTitle(task({ task: "   \n\t ", workflow: { state: "queued", awaitingHost: false, hostId: "h", ownerGeneration: 1, revision: 0, resultSummary: null } })).text)
      .toBe("未命名委派");
    expect(taskTitle(task({ task: "", workflow: undefined })).source).toBe("none");
    expect(taskTitle(task({ task: "", workflow: undefined })).text).toBe("未命名委派");
  });

  it("treats empty, whitespace and malformed non-string titles as absent", () => {
    const workflow = (title: unknown) => ({
      state: "delivered", awaitingHost: false, hostId: "h", ownerGeneration: 1, revision: 2, title,
    }) as Task["workflow"];
    for (const broken of ["", "  \r\n\t ", null, undefined, 7, false, { text: "对象" }, ["数组"]]) {
      expect(taskTitle(task({ workflow: workflow(broken) })).text).toBe("第一行标题");
      expect(taskTitle(task({ workflow: workflow(broken) })).source).toBe("task");
    }
  });

  it("preserves the existing trimmed-task fallback across leading blank lines", () => {
    expect(taskTitle(task({ task: " \r\n\t\n实际任务\r\n原始说明" })).text).toBe("实际任务");
  });

  it("keeps the Unicode-safe 100-character excerpt for headings and rows", () => {
    const long = "长".repeat(120);
    expect(excerpt(long, 100)).toBe("长".repeat(100) + "…");
    // Surrogate pairs never split: the astral emoji counts as one character.
    const emoji = "😀".repeat(120);
    expect([...excerpt(emoji, 100)]).toHaveLength(101);
    expect(excerpt(emoji, 100).endsWith("…")).toBe(true);
    expect(excerpt("短标题", 100)).toBe("短标题");
  });
});
