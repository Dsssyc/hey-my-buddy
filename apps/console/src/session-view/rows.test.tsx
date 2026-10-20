import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { I18nextProvider } from "react-i18next";
import { createInstance } from "i18next";
import zh from "../i18n/zh/taskDetail";
import en from "../i18n/en/taskDetail";
import { TooltipProvider } from "../components/ui/tooltip";
import { MessageScrollerProvider } from "../components/ui/message-scroller";
import { SessionBlocks, CallSummary } from "./SessionBlocks";
import { MessageResponse } from "./Markdown";
import { HighlightedCode } from "./MarkdownCode";
import { parseSession, records } from "./types";
import { sessionBlocks } from "./blocks";
import { sessionRows, toolObject, displayPath, unwrappedCommand, taskExcerpt, answerContent, markerBody } from "./rows";

const project = "/home/worker/project";
const tool = (id: string, kind = "read", extra = {}) => ({ sessionUpdate: "tool_call", toolCallId: id, title: id, kind, status: "completed", ...extra });
const message = (text: string, extra = {}) => ({ sessionUpdate: "agent_message_chunk", content: { type: "text", text }, ...extra });
const rows = (updates: unknown[]) => sessionRows(sessionBlocks(records(parseSession({ state: "ended", stopped: null, omitted: [], before: 0, after: 10, hasEarlier: false, events: updates.map(update => ({ sessionId: "attempt", update })) }))));
afterEach(cleanup);

describe("session rows", () => {
  it("groups adjacent tools across type-only markers, counts categories and failures, and keeps real prose in order", () => {
    const result = rows([message("I will read"), tool("a"), message("[collabAgentToolCall]", { _meta: { nativeItemId: "m", contentSegment: "main", marker: true, nativeType: "collabAgentToolCall" } }), tool("b", "execute", { status: "failed" }), tool("c"), message("I found the cause"), tool("d", "edit"), message("[review]\nReview details", { _meta: { nativeItemId: "review", contentSegment: "main", marker: true } }), message("Done")]);
    expect(result.map(row => row.type)).toEqual(["note", "calls", "note", "tool", "marker", "answer"]);
    expect(result[1]).toMatchObject({ counts: { read: 2, execute: 1 }, failed: 1, items: [{ key: "tool:a" }, { type: "message" }, { key: "tool:b" }, { key: "tool:c" }] });
    expect(rows([tool("one"), message("[activity]", { _meta: { nativeItemId: "m", contentSegment: "main", marker: true } })])[0]).toMatchObject({ type: "tool", markers: [{ type: "message" }] });
  });
  it("opens the first call independently while its group stays open", () => {
    const expanded = new Set<string>();
    const result = rows([tool("first"), tool("second")]);
    const view = () => <TooltipProvider><MessageScrollerProvider><SessionBlocks rows={result} taskText="Task" checkoutRoot={project} expansion={{ expanded: new Set(expanded), toggle: (key, open) => { if (open) expanded.add(key); else expanded.delete(key); rerender(view()); } }}/></MessageScrollerProvider></TooltipProvider>;
    const { rerender } = render(view());
    const group = screen.getByRole("button", { name: "读取 2 个文件" });
    fireEvent.click(group);
    const first = screen.getByRole("button", { name: /^first/ });
    expect(first.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(first);
    expect(first.getAttribute("aria-expanded")).toBe("true");
    expect(group.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: /^second/ }).getAttribute("aria-expanded")).toBe("false");
  });
  it("separates every prefixed blackboard action from call groups and keeps its details", () => {
    const result = rows([tool("read"), ...["checkpoint", "answer_inquiry", "finish_turn"].map((name, i) => tool(`${i}`, "other", { title: `mcp__blackboard__hey_my_buddy_${name}`, rawInput: { result: i } })), tool("edit", "edit")]);
    expect(result.map(row => row.type)).toEqual(["tool", "blackboard", "blackboard", "blackboard", "tool"]);
    expect(result[2]).toMatchObject({ action: "answer_inquiry", tool: { update: { rawInput: { result: 1 } } } });
  });
  it("keeps interim messages as notes when tools follow, and lets blackboard actions follow an answer", () => {
    expect(rows([{ ...message("Role prompt"), sessionUpdate: "user_message_chunk" }, { ...message("Thinking"), sessionUpdate: "agent_thought_chunk" }, message("First"), tool("call"), message("Last"), tool("after")]).map(row => row.type)).toEqual(["task", "reasoning", "note", "tool", "note", "tool"]);
    expect(rows([message("Done"), tool("checkpoint", "other", { title: "mcp__hmb__hey_my_buddy_checkpoint" })]).map(row => row.type)).toEqual(["answer", "blackboard"]);
    expect(taskExcerpt("# Title\n\nFirst\nSecond\nThird")).toEqual({ title: "Title", opening: "First\nSecond" });
  });
  it("uses native StructuredOutput outcome as the answer instead of the interim note", () => {
    const outcome = { summary: "Complete", remaining: [], request: null };
    const result = rows([message("I found the source"), tool("finish", "other", { title: "StructuredOutput", rawInput: { outcome } }), { sessionUpdate: "tool_call_update", toolCallId: "finish", status: "in_progress" }, { sessionUpdate: "tool_call_update", toolCallId: "finish", status: "completed" }]);
    expect(result.map(row => row.type)).toEqual(["note", "answer"]);
    expect(result[1]).toMatchObject({ content: { format: "fields", value: outcome } });
    expect(answerContent(' {"buddyId":null,"note":"No candidate","evidence":[]} ')).toMatchObject({ format: "fields", value: { buddyId: null } });
    expect(answerContent("ordinary prose")).toEqual({ format: "markdown", text: "ordinary prose" });
  });
  it("keeps diff markers collapsed and renders their literal body as diff code, never Markdown", async () => {
    const text = "[turn/diff/updated]\ndiff --git a/test.py b/test.py\n--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n- old\n+ **literal** <img src=x>";
    const result = rows([message(text, { _meta: { nativeItemId: "turn", contentSegment: "turn/diff/0", marker: true, nativeType: "turn/diff/updated", replace: true } })]);
    const expanded = new Set<string>();
    const view = () => <TooltipProvider><MessageScrollerProvider><SessionBlocks rows={result} taskText="Task" checkoutRoot={project} expansion={{ expanded: new Set(expanded), toggle: (key, open) => { if (open) expanded.add(key); else expanded.delete(key); rerender(view()); } }}/></MessageScrollerProvider></TooltipProvider>;
    const { container, rerender } = render(view());
    expect(container.textContent).not.toContain("diff --git");
    fireEvent.click(screen.getByRole("button", { name: "本回合的改动" }));
    expect(container.textContent).toContain("**literal** <img src=x>");
    expect(container.textContent).not.toContain("[turn/diff/updated]");
    expect(container.querySelector("img,strong,[data-streamdown]")).toBeNull();
    expect(result[0].type === "marker" && markerBody(result[0].message)).toBe(text.slice(text.indexOf("\n") + 1));
    await waitFor(() => expect(container.querySelector('[style*="--shiki-light"]')).not.toBeNull());
  });
  it("shows command output without decomposed commandActions, cwd or other input fields", () => {
    const result = rows([tool("run", "execute", { rawInput: { command: "echo done", cwd: project, commandActions: [{ type: "unknown", command: "echo done" }] }, content: [{ type: "content", content: { type: "text", text: "done" } }] })]);
    const { container } = render(<TooltipProvider><MessageScrollerProvider><SessionBlocks rows={result} taskText="Task" checkoutRoot={project} expansion={{ expanded: new Set(["tool:run"]), toggle: () => {} }}/></MessageScrollerProvider></TooltipProvider>);
    expect(container.textContent).toContain("done");
    expect(container.textContent).not.toContain("commandActions");
    expect(container.textContent).not.toContain("cwd");
    expect(container.querySelector("dl")).toBeNull();
  });
  it("labels a review Router task separately and lays its JSON answer out in buddy, note, evidence order", () => {
    const prompt = "ROLE: Router buddy\nPick within the review constraints.";
    const result = rows([{ ...message(prompt), sessionUpdate: "user_message_chunk" }, message(JSON.stringify({ evidence: ["Allowed"], note: "Fits", buddyId: "chosen", extra: { empty: [], other: {} } }))]);
    const first = result[0];
    if (first.type !== "task") throw Error("Expected task");
    const { container } = render(<TooltipProvider><MessageScrollerProvider><SessionBlocks rows={result} taskText="# Micro task title\nOpening" checkoutRoot={project} routingMode="review" expansion={{ expanded: new Set([first.key]), toggle: () => {} }}/></MessageScrollerProvider></TooltipProvider>);
    expect(container.textContent).toContain("为这个微任务选择 buddy（审阅模式）");
    expect(container.textContent).toContain("Micro task title");
    for (const line of prompt.split("\n")) expect(container.textContent).toContain(line);
    const answer = container.querySelector('[data-session-row="answer"]')!;
    expect([...answer.querySelectorAll(":scope [data-slot=session-answer-fields] > div > dt")].slice(0, 3).map(node => node.textContent)).toEqual(["选中的 buddy", "说明", "依据"]);
    expect(answer.textContent).toContain("chosen");
    expect(answer.textContent).toContain("[]");
    expect(answer.textContent).toContain("{}");
    expect(answer.querySelector("pre")).toBeNull();
  });
  it("extracts objects by category and shortens only paths, keeping commands and URLs intact", () => {
    const objects = [
      tool("Read", "read", { rawInput: { file_path: project + "/src/main.py" } }),
      tool("Edit", "edit", { rawInput: { notebook_path: "/home/worker/notes.ipynb" } }),
      tool("Run", "execute", { rawInput: { command: "/bin/zsh -lc 'rg main\nprintf done'" } }),
      tool("Search", "search", { rawInput: { pattern: "main", glob: "src/**" } }),
      tool("Fetch", "fetch", { rawInput: { url: "https://example.com" } }),
      tool("No input", "other", { locations: [{ path: project + "/README.md" }] }),
      tool("Old title", "read"),
    ];
    const blocks = sessionBlocks(records(parseSession({ state: "ended", stopped: null, omitted: [], before: 0, after: 1, hasEarlier: false, events: objects.map(update => ({ sessionId: "x", update })) })));
    expect(blocks.map(block => block.type === "tool" && toolObject(block.update, project))).toEqual(["src/main.py", "~/notes.ipynb", "rg main", "main · src/**", "https://example.com", "README.md", "Old title"]);
    expect(displayPath(project + "-other/src", project)).toBe("~/project-other/src");
    expect(displayPath("C:\\Users\\worker\\notes.txt", project)).toBe("~\\notes.txt");
    expect(unwrappedCommand('bash -c "echo test"')).toBe("echo test");
  });
  for (const [language, expected] of [["zh", "读取 2 个文件 · 执行 1 条命令1 次失败"], ["en", "Read 2 files · Ran 1 command1 failure"]]) it(`summarizes counts and failures in ${language}`, async () => {
    const i18n = createInstance(); await i18n.init({ lng: language, resources: { zh: { taskDetail: zh }, en: { taskDetail: en } } });
    const group = rows([tool("a"), tool("b"), tool("c", "execute", { status: "failed" })])[0];
    if (group.type !== "calls") throw Error("Expected calls");
    const { container } = render(<I18nextProvider i18n={i18n}><CallSummary group={group}/></I18nextProvider>);
    expect(container.textContent).toBe(expected);
  });
  it("renders one disclosure per call with direct fields and a lossless output preview", async () => {
    const result = rows([tool("search", "search", { rawInput: { pattern: "main", glob: "**/*.py", path: project }, content: [{ type: "content", content: { type: "text", text: Array.from({ length: 20 }, (_, n) => `line ${n}`).join("\n") } }] })]);
    const expanded = new Set(["tool:search"]);
    const view = (next: Set<string>) => <TooltipProvider><MessageScrollerProvider><SessionBlocks rows={result} taskText="# Task\nOpening" checkoutRoot={project} expansion={{ expanded: next, toggle: (key, open) => { if (open) expanded.add(key); else expanded.delete(key); rerender(view(new Set(expanded))); } }}/></MessageScrollerProvider></TooltipProvider>;
    const { container, rerender } = render(view(expanded));
    expect(screen.getByText("pattern")).toBeTruthy();
    expect(screen.getByText(".")).toBeTruthy();
    expect(container.textContent).toContain("line 13"); expect(container.textContent).not.toContain("line 19");
    fireEvent.click(screen.getByRole("button", { name: "展开其余 6 行" }));
    expect(container.textContent).toContain("line 19");
    expect(container.querySelectorAll('[data-slot="trace-collapsible"]')).toHaveLength(1);
  });
  it("renders ANSI output without escape sequences or links and keeps every fenced-code line", async () => {
    const { container } = render(<><HighlightedCode code={"\u001b[31mfailed\u001b[0m https://example.com"} language="ansi"/><MessageResponse>{"```text\n" + Array.from({ length: 20 }, (_, index) => `line ${index}`).join("\n") + "\n```"}</MessageResponse></>);
    await waitFor(() => expect(container.textContent).not.toContain("\u001b[31m"));
    expect(container.textContent).toContain("failed https://example.com");
    expect(container.textContent).toContain("line 19");
    expect(container.querySelector("a")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
  it("keeps model HTML, images and local or unsafe links inert while rendering Markdown", async () => {
    const { container } = render(<MessageResponse>{'**bold** [web](https://example.com/path) [local](file:///tmp/private) [loopback](http://127.0.0.1/private) [script](javascript:alert(1)) ![remote](https://example.com/pixel.png) <img src="https://example.com/raw.png">'}</MessageResponse>);
    await waitFor(() => expect(screen.getByText("bold")).toBeTruthy());
    expect(container.querySelectorAll("a[href]")).toHaveLength(1);
    expect(container.querySelector("a")?.getAttribute("href")).toBe("https://example.com/path");
    expect(container.querySelector("img")).toBeNull();
    expect(container.innerHTML).not.toContain("javascript:");
  });
});
