import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useTextMeasure } from "./labels";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("leaves hidden nonempty text unmeasured and measures again when its SVG is revealed", () => {
  let width = 0;
  let resized: () => void = () => {};
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: () => void) { resized = callback; }
    observe() {} disconnect() {}
  });
  vi.spyOn(SVGElement.prototype as SVGTextElement, "getComputedTextLength").mockImplementation(() => width);
  function Drawing({ text }: { text: string }) {
    const { measure, ruler } = useTextMeasure([text]);
    return <svg>{ruler}<text data-slot="measured">{String(measure(text))}</text></svg>;
  }
  const view = render(<Drawing text="26 min"/>);
  const measured = () => view.container.querySelector('[data-slot=measured]')!.textContent;
  expect(measured()).toBe("Infinity");
  width = 42;
  act(() => resized());
  expect(measured()).toBe("42");
  // A poll can supply a new duration while the drawing's page is hidden.
  width = 0;
  view.rerender(<Drawing text="27 min"/>);
  expect(measured()).toBe("Infinity");
  width = 49;
  act(() => resized());
  expect(measured()).toBe("49");
});
