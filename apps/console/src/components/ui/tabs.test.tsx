import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Tabs, TabsList, TabsTrigger } from "./tabs";

describe("TabsTrigger focus presentation", () => {
  it("suppresses pointer-initiated focus-visible styling without dropping focus and restores it on keyboard use or blur", () => {
    render(
      <Tabs defaultValue="all">
        <TabsList variant="line" aria-label="Filter">
          <TabsTrigger value="all">All</TabsTrigger>
          <TabsTrigger value="active">Running</TabsTrigger>
        </TabsList>
      </Tabs>,
    );
    const active = screen.getByRole("tab", { name: "Running" });

    expect(active.className).toContain("outline-none");
    expect(active.className).not.toContain("focus-visible:outline-1");

    // Non-pointer events (e.g. synthetic keyboard activation) never mark pointerFocus.
    fireEvent.pointerDown(active, { pointerType: "" });
    expect(active.dataset.pointerFocus).toBeUndefined();

    // Mouse/touch pointer interaction marks pointerFocus so Radix's onMouseDown
    // focus() call cannot paint a false keyboard focus box on a fresh document.
    fireEvent.pointerDown(active, { pointerType: "mouse" });
    expect(active.dataset.pointerFocus).toBe("true");

    // Any key interaction immediately clears pointerFocus for keyboard navigation.
    fireEvent.keyDown(active, { key: "ArrowRight" });
    expect(active.dataset.pointerFocus).toBeUndefined();

    // Blur also clears pointerFocus.
    fireEvent.pointerDown(active, { pointerType: "touch" });
    expect(active.dataset.pointerFocus).toBe("true");
    fireEvent.blur(active);
    expect(active.dataset.pointerFocus).toBeUndefined();
  });
});
