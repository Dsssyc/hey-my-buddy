import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { Hint } from "./hint";
import { Button } from "./ui/button";
import { TooltipProvider } from "./ui/tooltip";

afterEach(cleanup);

function Page({ text }: { text?: string }) {
  return <TooltipProvider><Hint text={text}><button type="button">Check</button></Hint></TooltipProvider>;
}

describe("Hint", () => {
  it("keeps the same button, with its focus, when the text comes and goes", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<Page />);
    await user.tab();
    const button = screen.getByRole("button", { name: "Check" });
    expect(document.activeElement).toBe(button);
    expect(button.getAttribute("aria-description")).toBeNull();

    rerender(<Page text="Busy" />);
    expect(screen.getByRole("button", { name: "Check" })).toBe(button);
    expect(document.activeElement).toBe(button);
    expect(button.getAttribute("aria-description")).toBe("Busy");

    rerender(<Page />);
    expect(screen.getByRole("button", { name: "Check" })).toBe(button);
    expect(document.activeElement).toBe(button);
    expect(button.getAttribute("aria-description")).toBeNull();
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("leaves the control's own data-slot, with or without text", () => {
    const page = (text?: string) => <TooltipProvider><Hint text={text}><Button>Check</Button></Hint></TooltipProvider>;
    const { rerender } = render(page());
    expect(screen.getByRole("button", { name: "Check" }).getAttribute("data-slot")).toBe("button");
    rerender(page("Busy"));
    expect(screen.getByRole("button", { name: "Check" }).getAttribute("data-slot")).toBe("button");
  });

  it("shows the text on hover only while there is some", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<Page />);
    await user.hover(screen.getByRole("button", { name: "Check" }));
    expect(screen.queryByRole("tooltip")).toBeNull();
    rerender(<Page text="Busy" />);
    await user.unhover(screen.getByRole("button", { name: "Check" }));
    await user.hover(screen.getByRole("button", { name: "Check" }));
    expect((await screen.findByRole("tooltip")).textContent).toBe("Busy");
  });
});
