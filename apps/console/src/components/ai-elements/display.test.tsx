import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CodeBlock, MessageResponse, Reasoning, ReasoningContent, ReasoningTrigger, Terminal, Tool, ToolContent, ToolHeader } from ".";
import { TooltipProvider } from "../ui/tooltip";

afterEach(() => { cleanup(); vi.restoreAllMocks(); Reflect.deleteProperty(navigator, "clipboard"); });

describe("session transcript display", () => {
  it("renders Markdown while keeping raw HTML, local links, unsafe schemes, and remote images inert", async () => {
    const markdown = "**bold** [web](https://example.com/path) [local](file:///tmp/private) [loopback](http://127.0.0.1/private) [intranet](http://192.168.1.1/private) [script](javascript:alert(1)) ![remote](https://example.com/pixel.png) <img src=\"https://example.com/raw.png\">";
    const { container } = render(<MessageResponse>{markdown}</MessageResponse>);
    await waitFor(() => expect(screen.getByText("bold").getAttribute("data-streamdown")).toBe("strong"));
    expect(container.querySelectorAll("a[href]")).toHaveLength(1);
    expect(container.querySelector("a[href]")?.getAttribute("href")).toBe("https://example.com/path");
    expect(container.querySelector("img")).toBeNull();
    expect(container.innerHTML).not.toContain("file:///");
    expect(container.innerHTML).not.toContain("javascript:");
  });

  it("opens reasoning and tool details with their caller supplied labels", () => {
    render(<><Reasoning><ReasoningTrigger>推理</ReasoningTrigger><ReasoningContent>**内容**</ReasoningContent></Reasoning>
      <Tool><ToolHeader title="读取文件" category="工具" status="已完成" /><ToolContent>结果</ToolContent></Tool></>);
    const reasoning = screen.getByRole("button", { name: "推理" });
    expect(reasoning.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(reasoning);
    expect(reasoning.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("内容")).toBeTruthy();
    const tool = screen.getByRole("button", { name: /读取文件.*工具.*已完成/ });
    expect(tool.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(tool);
    expect(tool.getAttribute("aria-expanded")).toBe("false");
  });

  it("renders ANSI colour without creating a link from terminal output", () => {
    const { container } = render(<Terminal output={"\u001b[31mfailed\u001b[0m https://example.com"} />);
    expect(container.textContent).toContain("failed https://example.com");
    expect(container.querySelector("a")).toBeNull();
  });

  it("shows fenced Markdown code by default, lets the reader fold it, and copies its source", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: write } });
    const { container } = render(<TooltipProvider><MessageResponse>{"```text\nfirst\nsecond\n```"}</MessageResponse></TooltipProvider>);
    expect(container.querySelector('[data-slot="session-code"]')?.textContent).toBe("first\nsecond\n");
    const copy = screen.getByRole("button", { name: "复制全文" });
    expect(copy.querySelector("svg")).toBeTruthy();
    expect(copy.textContent).toBe("");
    fireEvent.click(copy);
    await waitFor(() => expect(copy.querySelector("svg")?.classList.contains("lucide-check")).toBe(true));
    expect(write).toHaveBeenCalledWith("first\nsecond\n");
    const disclosure = screen.getByRole("button", { name: /收起代码/ });
    fireEvent.click(disclosure);
    expect(disclosure.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector('[data-slot="session-code"]')).toBeNull();
    fireEvent.click(disclosure);
    expect(container.querySelector('[data-slot="session-code"]')?.textContent).toBe("first\nsecond\n");
  });

  it("loads a supported grammar after code mounts and retains both theme tokens", async () => {
    const { container } = render(<CodeBlock code={'{"ok":true}'} language="json" />);
    await waitFor(() => expect(container.querySelectorAll("pre")).toHaveLength(2));
    expect(container.querySelectorAll("span[style*='color']").length).toBeGreaterThan(0);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
