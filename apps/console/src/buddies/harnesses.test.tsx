import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { HARNESS_ORDER } from "../api";
import { harnessName } from "../buddy-display";
import { HarnessLogo } from "./HarnessLogo";

afterEach(cleanup);

/**
 * The rendered mark and display name of every harness the board can run: each
 * id the Buddy page orders must draw a real mark — a coloured <img> or the
 * masked mark — never the blank spacer, and must read as its product name, not
 * the unmapped id. An unknown id stays an honest blank spacer and the id
 * itself. The cross-language inventory (the order holding exactly the board's
 * harness names) is the Python conformance suite's check.
 */

function markOf(harness: string): { img: Element | null; masked: Element | null } {
  const { container } = render(<HarnessLogo harness={harness} />);
  return { img: container.querySelector("img"), masked: container.querySelector('[data-slot="harness-logo"]') };
}

describe("the harness mark of every registered harness", () => {
  it("orders every harness once", () => {
    expect(HARNESS_ORDER.length).toBeGreaterThan(0);
    expect(new Set(HARNESS_ORDER).size).toBe(HARNESS_ORDER.length);
  });

  it("renders a real mark, never a blank spacer, for every ordered harness", () => {
    for (const harness of HARNESS_ORDER) {
      const { img, masked } = markOf(harness);
      if (img) {
        expect(img.getAttribute("src"), `${harness} draws an img without a source`).toBeTruthy();
        expect(masked, `${harness} draws both an img and a masked mark`).toBeNull();
      } else {
        expect(masked, `${harness} renders neither an img nor a masked mark`).toBeTruthy();
        expect((masked as HTMLElement).style.maskImage, `${harness} masks its mark with nothing`).toMatch(/url\(.+\)/);
      }
    }
  });

  it("renders a blank spacer without the mark slot for an unknown harness", () => {
    const { container } = render(<HarnessLogo harness="unknown-harness" />);
    expect(container.querySelector('[data-slot="harness-logo"]')).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("span")).toBeTruthy();
  });
});

describe("the display name of every registered harness", () => {
  it("names every ordered harness distinctly, never the unmapped id", () => {
    const names = HARNESS_ORDER.map(harnessName);
    expect(new Set(names).size).toBe(names.length);
    for (const harness of HARNESS_ORDER) {
      expect(harnessName(harness), `${harness} has no display name`).toBeTruthy();
      expect(harnessName(harness), `${harness} shows its unmapped id`).not.toBe(harness);
    }
  });

  it("shows an unknown harness id as recorded", () => {
    expect(harnessName("unknown-harness")).toBe("unknown-harness");
  });
});
