import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { fixture, harness, openHarness } from "./harness.fixtures";
import { harnessSection } from "./harness.testing";
import { catalogue, effortCell, foldHandle, modelButton, selectModel } from "./page.testing";

/**
 * The gestures of the catalogue, apart from what they open: a harness's whole
 * head controls its models while details remain in the sidebar. The model drawer opens
 * through the shared Collapsible. How they move and how they change colour is
 * a matter of computed style, which the browser checks assert.
 */

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  window.location.hash = "";
});

const header = (name: string) => harnessSection(name).querySelector<HTMLElement>("[data-slot=harness-header]")!;

describe("a harness section's head", () => {
  it("has one full-header trigger, a decorative arrow and separate retries", async () => {
    await openHarness(fixture(), "#buddies/models");
    for (const name of ["DSH", "ZCode", "Codex", "Claude Code"]) {
      const buttons = within(header(name)).getAllByRole("button");
      expect(buttons[0].getAttribute("data-slot")).toBe("fold-handle");
      expect(buttons.slice(1).every(button => button.textContent === "开放重试")).toBe(true);
      const [fold] = buttons;
      expect(fold.getAttribute("aria-label")).toMatch(new RegExp(`^(展开|收起) ${name}$`));
      expect(fold.hasAttribute("aria-expanded")).toBe(true);
      expect(header(name).querySelectorAll("button button, [tabindex]:not(button)")).toHaveLength(0);
      // One chevron; the cooldown summary has none of its own.
      expect(header(name).querySelectorAll("[data-slot=disclosure]")).toHaveLength(1);
      expect(fold.children).toHaveLength(0);
      expect(fold.parentElement).toBe(header(name));
      expect(header(name).textContent).not.toMatch(/[▲▼▶▷△▽▸▾]/);
      // The shared icon uses a 16px chevron with the default stroke.
      const mark = header(name).querySelector("[data-slot=disclosure]")!;
      expect(mark.getAttribute("aria-hidden")).toBe("true");
      expect(mark.getAttribute("class")).toContain("lucide-chevron-right");
      expect(mark.getAttribute("width")).toBe("16");
      expect(mark.getAttribute("stroke-width")).toBe("2");
      expect(mark.getAttribute("fill")).toBe("none");
    }
  });

  it("folds models while harness details stay in the sidebar", async () => {
    const user = userEvent.setup();
    await openHarness(fixture({ harnesses: [harness({ harness: "claude", status: "ready" })] }), "#buddies/models");
    const fold = foldHandle("Claude Code");
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    expect(header("Claude Code").textContent).toContain("Claude Code");
    expect(header("Claude Code").textContent).toContain("anthropic");
    const details = document.querySelector("[data-slot=harness-details][aria-label=\"Claude Code 设置\"]");
    expect(details).toBeTruthy();
    await user.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(details).toBeTruthy();
    await user.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    expect(details).toBeTruthy();
  });

  it("folds models from the keyboard by Enter and Space", async () => {
    const user = userEvent.setup();
    await openHarness(fixture(), "#buddies/models");
    const fold = foldHandle("Codex");
    const mark = header("Codex").querySelector("[data-slot=disclosure]")!;
    expect(mark.getAttribute("data-state")).toBe("closed");
    expect(mark.classList.contains("rotate-90")).toBe(false);
    fold.focus();
    await user.keyboard("{Enter}");
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    expect(mark.getAttribute("data-state")).toBe("open");
    expect(mark.classList.contains("rotate-90")).toBe(true);
    await user.keyboard(" ");
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(mark.getAttribute("data-state")).toBe("closed");
    expect(mark.classList.contains("rotate-90")).toBe(false);
    expect(document.querySelector("[data-slot=harness-details][aria-label=\"Codex 设置\"]")).toBeTruthy();
  });

  it("shows a sidebar card even for a harness whose models are folded away", async () => {
    await openHarness(fixture({ harnesses: [harness({ harness: "zcode", status: "missing" })] }), "#buddies/models");
    expect(foldHandle("ZCode").getAttribute("aria-expanded")).toBe("false");
    expect(document.getElementById("harness-title-zcode")?.closest("[data-slot=harness-card]")).toBeTruthy();
    expect(document.querySelector("[data-slot=harness-details][aria-label=\"ZCode 设置\"]")).toBeTruthy();
    expect(foldHandle("ZCode").getAttribute("aria-expanded")).toBe("false");
  });
});

describe("what opens and closes", () => {
  it("uses a Collapsible for models and model drawers while details remain visible", async () => {
    const user = userEvent.setup();
    await openHarness(fixture({ harnesses: [harness({ harness: "claude", status: "ready" })] }), "#buddies/models");
    const contents = (scope: HTMLElement) => [...scope.querySelectorAll<HTMLElement>("[data-slot=collapsible-content]")];
    const section = harnessSection("Claude Code");
    expect(section.getAttribute("data-state")).toBe("open");
    expect(document.querySelector("[data-slot=harness-details]")).toBeTruthy();
    await user.click(modelButton("claude / anthropic / claude-sonnet-5"));
    const drawer = section.querySelector<HTMLElement>("[data-slot=model-drawer]")!;
    expect(drawer.closest("[data-slot=collapsible-content]")!.getAttribute("data-state")).toBe("open");
    // Every one moves on the 200ms height Radix measures, and not at all for a reader who asks for less motion.
    for (const content of contents(section)) {
      expect(content.className).toContain("animation-duration-200");
      expect(content.className).toContain("motion-safe:data-[state=open]:animate-collapsible-down");
      expect(content.className).toContain("motion-safe:data-[state=closed]:animate-collapsible-up");
    }
    expect(foldHandle("Claude Code").getAttribute("aria-controls")).toBe(section.querySelector(":scope > [data-slot=collapsible-content]")!.id);
    await user.click(foldHandle("Claude Code"));
    expect(section.getAttribute("data-state")).toBe("closed");
    expect(section.querySelector("[data-slot=model-drawer]")).toBeNull();
    // Opened again, the model the user had open is open still.
    await user.click(foldHandle("Claude Code"));
    await waitFor(() => expect(section.querySelector("[data-slot=model-drawer]")).toBeTruthy());
  });

  it("opens a model's drawer from its row, keeps one open at a time and keeps the effort chosen on each", async () => {
    const user = userEvent.setup();
    await openHarness(fixture({ harnesses: [harness({ harness: "claude", status: "ready" })] }), "#buddies/models");
    const identity = "claude / anthropic / claude-sonnet-5";
    await selectModel(user, identity, "Claude Code");
    await user.click(effortCell("medium"));
    const row = modelButton(identity);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(row.querySelector("[data-slot=disclosure]")).toBeNull();
    expect(row.querySelector("[data-slot=model-chevron]")?.getAttribute("aria-hidden")).toBe("true");
    await user.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("false");
    expect(row.querySelector("[data-slot=disclosure]")).toBeNull();
    expect(row.querySelector("[data-slot=model-chevron]")).toBeTruthy();
    expect(catalogue().querySelector("[data-slot=model-drawer]")).toBeNull();
  });
});

describe("the controls band", () => {
  it("keeps the search apart and the rest in one group whose two buttons never part", async () => {
    await openHarness(fixture(), "#buddies/models");
    const controls = document.querySelector<HTMLElement>("[data-slot=catalogue-controls]")!;
    const group = controls.querySelector<HTMLElement>("[data-slot=catalogue-group]")!;
    const search = screen.getByRole("searchbox", { name: "搜索 buddy" });
    expect(search.parentElement).toBe(controls);
    expect(group.contains(search)).toBe(false);
    expect(group.parentElement).toBe(controls);
    expect([...group.children].map(child => child.getAttribute("data-slot") ?? child.getAttribute("role"))).toEqual(["catalogue-filters", "catalogue-actions"]);
    const actions = group.querySelector<HTMLElement>("[data-slot=catalogue-actions]")!;
    expect(within(actions).getAllByRole("button").map(button => button.textContent)).toEqual(["发现模型"]);
    // The search and controls can wrap; the two actions stay together.
    expect(controls.className).toContain("flex-wrap");
    expect(group.className).toContain("flex-wrap");
    expect(actions.className).not.toContain("flex-wrap");
    expect(actions.className).toContain("flex");
  });
});
