import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Hint } from "./hint";
import { TooltipProvider } from "./ui/tooltip";
import { useFadingText } from "./use-fading-text";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function Label({ text }: { text: string }) {
  const label = useFadingText(text);
  return <TooltipProvider delayDuration={0}><Hint text={label.hint}><button type="button">
    <span ref={label.ref} className={label.className}>{text}</span>
  </button></Hint></TooltipProvider>;
}

describe("useFadingText", () => {
  it.each([
    { scrollWidth: 80, clipped: false },
    { scrollWidth: 100, clipped: false },
    { scrollWidth: 140, clipped: true },
  ])("fades and reveals the full text only when it overflows ($scrollWidth / 100)", async ({ scrollWidth, clipped }) => {
    // jsdom supplies the measured overflow; the browser checks real glyph widths.
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(100);
    vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockReturnValue(scrollWidth);
    const text = "schema 26 的冗余清点";
    render(<Label text={text}/>);
    const label = screen.getByText(text);
    const button = screen.getByRole("button", { name: text });
    expect(label.classList.contains("text-fade")).toBe(clipped);
    expect(label.classList.contains("truncate")).toBe(!clipped);
    expect(button.getAttribute("aria-description")).toBe(clipped ? text : null);
    await userEvent.hover(label);
    if (clipped) expect((await screen.findByRole("tooltip")).textContent).toBe(text);
    else expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
