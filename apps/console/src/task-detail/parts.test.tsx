import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Fold, Lines, Paths, Moment, MomentRange } from "./parts";

afterEach(cleanup);

describe("model-authored Markdown", () => {
  it("renders headings, lists and inline code without cutting the source tree", () => {
    const text = "# 任务书\n\n- 验证 `layout.tsx`\n- **核对**结果\n\n" + "长文完整保留。".repeat(120);
    const { container } = render(<Fold text={text} />);
    expect(screen.getByRole("heading", { name: "任务书" })).toBeTruthy();
    expect(container.querySelector("li code")?.textContent).toBe("layout.tsx");
    expect(container.querySelector("strong")?.textContent).toBe("核对");
    expect(container.querySelector("[data-slot=fold-body]")?.textContent).toContain("长文完整保留。".repeat(120));
  });

  it("renders inline Markdown in model lists while product paths stay literal", () => {
    const { container } = render(<><Lines items={["修复 `a.ts`，**核对**改动"]} /><Paths items={["**literal**/a[1].ts"]} /></>);
    expect(container.querySelector("li code")?.textContent).toBe("a.ts");
    expect(container.querySelectorAll("strong")).toHaveLength(1);
    expect(screen.getByText("**literal**/a[1].ts")).toBeTruthy();
  });

  it("keeps the shared Markdown link and image safety rules", () => {
    const { container } = render(<Fold text="[web](https://example.com) [local](file:///private) [loopback](http://127.0.0.1/private) ![remote](https://example.com/pixel.png) <b>raw</b>" />);
    expect(screen.getByRole("link", { name: "web" }).getAttribute("rel")).toBe("noopener noreferrer");
    expect(container.querySelectorAll("a")).toHaveLength(1);
    expect(container.querySelector("img,b")).toBeNull();
  });
});

describe("a recorded instant", () => {
  it("is written to the second in local time, and says it is not recorded when it is not", () => {
    const now = new Date();
    const view = render(<><Moment at={now.getTime()} /><Moment at={null} /><Moment at="not a date" /></>);
    const times = view.container.querySelectorAll("time");
    expect(times[0]!.textContent).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    expect(times[0]!.getAttribute("datetime")).toBe(now.toISOString());
    expect(view.container.textContent).toContain("未记录未记录");
  });

  it("carries the date when it is not today, and the year when it is not this year", () => {
    const earlier = new Date(); earlier.setDate(earlier.getDate() - 2);
    const longAgo = new Date(); longAgo.setFullYear(longAgo.getFullYear() - 1);
    const view = render(<><Moment at={earlier.getTime()} /><Moment at={longAgo.getTime()} /></>);
    const [recent, old] = [...view.container.querySelectorAll("time")].map(item => item.textContent!);
    expect(recent).toMatch(/^(\d{4}-)?\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
    expect(old).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
    expect(old!.startsWith(String(longAgo.getFullYear()))).toBe(true);
  });
});

describe("a recorded interval", () => {
  it("writes the date once within a local day and at both ends across local days", () => {
    const first = new Date(2026, 8, 25, 23, 58, 4);
    const same = new Date(2026, 8, 25, 23, 59, 5);
    const next = new Date(2026, 8, 26, 0, 1, 6);
    const view = render(<><MomentRange start={first.getTime()} end={same.toISOString()}/><MomentRange start={first.getTime()} end={next.toISOString()}/></>);
    const [withinDay, acrossDays] = [...view.container.querySelectorAll("span.font-mono")];
    expect(withinDay?.textContent).toMatch(/09-25 23:58:04 – 23:59:05/);
    expect(acrossDays?.textContent).toMatch(/09-25 23:58:04 – 09-26 00:01:06/);
    expect(withinDay?.querySelectorAll("time")).toHaveLength(2);
  });
});
