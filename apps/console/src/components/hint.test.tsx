import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { Hint } from "./hint";
import { Button } from "./ui/button";
import { Tabs, TabsList, TabsTrigger } from "./ui/tabs";
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

  it("preserves a tab's selected state while its explanation opens and closes", async () => {
    const user = userEvent.setup();
    render(<TooltipProvider><Tabs defaultValue="host"><TabsList variant="line">
      <Hint text="Waiting for Host"><TabsTrigger value="host">Host</TabsTrigger></Hint>
      <TabsTrigger value="all">All</TabsTrigger>
    </TabsList></Tabs></TooltipProvider>);
    const host = screen.getByRole("tab", { name: "Host" });
    expect(host.getAttribute("aria-selected")).toBe("true");
    expect(host.getAttribute("data-state")).toBe("active");
    await user.hover(host);
    expect((await screen.findByRole("tooltip")).textContent).toBe("Waiting for Host");
    expect(host.getAttribute("data-state")).toBe("active");
    await user.click(screen.getByRole("tab", { name: "All" }));
    expect(host.getAttribute("aria-selected")).toBe("false");
    expect(host.getAttribute("data-state")).toBe("inactive");
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
