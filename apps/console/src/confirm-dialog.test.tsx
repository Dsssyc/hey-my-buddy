import { useState } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

/**
 * docs/decisions/016-human-console-design.md: a modal dialog keeps its focus
 * trap, and everything behind it is inert while it is open.
 */

afterEach(() => cleanup());

function Harness({ initiallyOpen = true }: { initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  return <div className="page-background">
    <button type="button" onClick={() => setOpen(true)}>打开确认</button>
    <button type="button">后台按钮</button>
    {open && <ConfirmDialog title="替换 Router" confirmLabel="替换"
      onCancel={() => setOpen(false)} onConfirm={() => setOpen(false)}>确认正文</ConfirmDialog>}
  </div>;
}

describe("ConfirmDialog", () => {
  it("makes the background inert while open and restores it on cancel", async () => {
    const user = userEvent.setup();
    const view = render(<Harness />);
    const background = view.container as HTMLElement;
    expect(background.hasAttribute("inert")).toBe(true);
    // The dialog itself stays reachable: it is portaled outside the background.
    const dialog = screen.getByRole("dialog", { name: "替换 Router" });
    expect(dialog.hasAttribute("inert")).toBe(false);

    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(background.hasAttribute("inert")).toBe(false);
  });

  it("restores the background on confirm and never clears a foreign inert", async () => {
    const user = userEvent.setup();
    const view = render(<Harness />);
    const background = view.container as HTMLElement;
    const inner = view.container.querySelector<HTMLElement>(".page-background")!;
    await user.click(screen.getByRole("button", { name: "替换" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(background.hasAttribute("inert")).toBe(false);

    // An element that was already inert for another reason stays inert.
    inner.setAttribute("inert", "");
    await user.click(screen.getByRole("button", { name: "打开确认" }));
    expect(inner.hasAttribute("inert")).toBe(true);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(inner.hasAttribute("inert")).toBe(true);
    expect(background.hasAttribute("inert")).toBe(false);
  });
});
