import { expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SessionCoverage } from "./SessionCoverage";
import { fixtureCoverage } from "./test-fixtures";

it("opens all five categories, distinguishes missing evidence and absence, and closes by keyboard", async () => {
  const user = userEvent.setup();
  render(<SessionCoverage harness="codex" coverage={fixtureCoverage}/>);
  expect(screen.queryByRole("table")).toBeNull();
  const trigger = screen.getByRole("button", { name: "会话内容" });
  await user.click(trigger);
  const table = screen.getByRole("table", { name: "会话内容" });
  expect(within(table).getAllByRole("row")).toHaveLength(5);
  expect(within(table).getByText("有证据 · fixture CLI 1")).not.toBeNull();
  expect(within(table).getAllByText("未核实")).toHaveLength(3);
  expect(within(table).getByText(/不提供：Codex 内部推理/)).not.toBeNull();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("table")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});
