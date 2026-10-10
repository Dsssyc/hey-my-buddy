import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { InlineText } from "./inline-text";
afterEach(cleanup);
it("keeps exactly one paragraph through expand/collapse and resets even for identical text on a new record", async () => {
  const text = "正文".repeat(300);
  const user = userEvent.setup();
  const view = render(<InlineText recordId="a" text={text} />);
  const p = view.container.querySelector("p")!;
  expect(p.textContent).toBe(text.slice(0, 360) + "… 展开");
  await user.click(screen.getByRole("button", { name: "展开" }));
  expect(view.container.querySelectorAll("p")).toHaveLength(1);
  expect(p.textContent).toBe(text + " 收起");
  expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");
  await user.click(screen.getByRole("button", { name: "收起" }));
  expect(p.textContent).toBe(text.slice(0, 360) + "… 展开");
  await user.click(screen.getByRole("button", { name: "展开" }));
  view.rerender(<InlineText recordId="b" text={text} />);
  expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("false");
  expect(p.textContent).toBe(text.slice(0, 360) + "… 展开");
  view.rerender(<InlineText recordId="a" text={text} />);
  expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("false");
});
it("shows short text without a disclosure", () => {
  render(<InlineText recordId="a" text="短文" />);
  expect(screen.getByText("短文")).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
});
