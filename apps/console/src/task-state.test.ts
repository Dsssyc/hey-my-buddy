import { describe, expect, it } from "vitest";
import type { Task } from "./types";
import { excerpt, taskTitle } from "./task-state";

const task = (overrides: Partial<Task> = {}): Task => ({
  runId: "run-1", task: "第一行标题\n第二行", status: "queued", owner: "host", cwd: "/repo",
  revision: 1, createdAt: "2026-09-26T00:00:00Z", acceptedAt: null, acceptanceVerdict: null,
  ...overrides,
});

describe("task title fallback", () => {
  it("prefers a nonempty resultSummary over the first task line", () => {
    expect(taskTitle(task({ workflow: { state: "delivered", awaitingHost: false, hostId: "h", ownerGeneration: 1, revision: 3, resultSummary: "已验证的整合结果" } })))
      .toBe("已验证的整合结果");
  });

  it("falls back to the trimmed first task line without a summary or workflow", () => {
    expect(taskTitle(task())).toBe("第一行标题");
    expect(taskTitle(task({ workflow: { state: "executing", awaitingHost: false, hostId: "h", ownerGeneration: 1, revision: 1, resultSummary: null } })))
      .toBe("第一行标题");
  });

  it("names an unnamed delegation only when the summary and first line are empty", () => {
    expect(taskTitle(task({ task: "   \n\t ", workflow: { state: "queued", awaitingHost: false, hostId: "h", ownerGeneration: 1, revision: 0, resultSummary: null } })))
      .toBe("未命名委派");
    expect(taskTitle(task({ task: "", workflow: undefined }))).toBe("未命名委派");
  });

  it("preserves the existing trimmed-task fallback across leading blank lines", () => {
    expect(taskTitle(task({ task: " \r\n\t\n实际任务\r\n原始说明" }))).toBe("实际任务");
  });

  it("treats empty, whitespace and malformed non-string summaries as no result", () => {
    const workflow = (resultSummary: unknown) => ({
      state: "delivered", awaitingHost: false, hostId: "h", ownerGeneration: 1, revision: 2, resultSummary,
    }) as Task["workflow"];
    for (const broken of ["", "  \r\n\t ", null, undefined, 7, false, { text: "对象" }, ["数组"]]) {
      expect(taskTitle(task({ workflow: workflow(broken) }))).toBe("第一行标题");
    }
  });

  it("normalizes display whitespace including CRLF, tabs and ideographic spaces", () => {
    expect(taskTitle(task({ task: "first\r\nsecond\nthird" }))).toBe("first");
    expect(taskTitle(task({ workflow: { state: "delivered", awaitingHost: false, hostId: "h", ownerGeneration: 1, revision: 2, resultSummary: "a\tb  c\r\nd　e" } })))
      .toBe("a b c d e");
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
