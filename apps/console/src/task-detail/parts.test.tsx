import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { Fold, Moment, MomentRange } from "./parts";

afterEach(cleanup);

describe("a text that may be long", () => {
  const text = "这是一段很长的原文。".repeat(60);

  it("is shown whole, with nothing to press, when it is short", () => {
    render(<Fold text="短文" />);
    expect(screen.getByText("短文")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("is folded after its head, unfolds in the same paragraph, folds again, and starts folded on a new text", async () => {
    const user = userEvent.setup();
    const view = render(<Fold text={text} limit={100} />);
    const paragraph = () => view.container.querySelectorAll("p");
    expect(paragraph()).toHaveLength(1);
    expect(paragraph()[0]!.textContent).toBe(text.slice(0, 100) + "…");
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("false");
    await user.click(screen.getByRole("button", { name: "展开" }));
    expect(paragraph()).toHaveLength(1);
    expect(paragraph()[0]!.textContent).toBe(text);
    expect(screen.getByRole("button", { name: "收起" }).getAttribute("aria-expanded")).toBe("true");
    await user.click(screen.getByRole("button", { name: "收起" }));
    expect(paragraph()[0]!.textContent).toBe(text.slice(0, 100) + "…");
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("false");
  });

  it("is cut at a line or a word that ends, not in the middle of one, and the cut loses nothing when unfolded", async () => {
    const lines = "第一行说了一件事\n第二行说了另一件事\n第三行说了第三件事";
    const view = render(<Fold text={lines} limit={14} />);
    expect(view.container.querySelector("p")!.textContent).toBe("第一行说了一件事…");
    await userEvent.setup().click(screen.getByRole("button", { name: "展开" }));
    expect(view.container.querySelector("p")!.textContent).toBe(lines);
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
