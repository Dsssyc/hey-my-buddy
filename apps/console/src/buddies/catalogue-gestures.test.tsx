import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { fixture, harness, openHarness } from "./harness.fixtures";
import { harnessButton, harnessSection } from "./harness.testing";
import { catalogue, effortCell, foldHandle, modelButton, selectModel } from "./page.testing";

/**
 * The gestures of the catalogue, apart from what they open: a harness's head is
 * two buttons that do not contain each other, one for the section's models and
 * one for its location, account and quota; and what opens and closes does so
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
  it("is two buttons side by side with a name and a state each, and no second arrow", async () => {
    await openHarness(fixture(), "#buddies", false);
    for (const name of ["DSH", "ZCode", "Codex", "Claude Code"]) {
      const buttons = within(header(name)).getAllByRole("button");
      expect(buttons.map(button => button.getAttribute("data-slot"))).toEqual(["fold-handle", "account-summary"]);
      const [fold, account] = buttons;
      expect(fold.getAttribute("aria-label")).toMatch(new RegExp(`^(展开|收起) ${name}$`));
      expect(account.getAttribute("aria-label")).toBe(`${name} 账户`);
      expect(fold.hasAttribute("aria-expanded")).toBe(true);
      expect(account.hasAttribute("aria-expanded")).toBe(true);
      expect(fold.contains(account) || account.contains(fold)).toBe(false);
      expect(header(name).querySelectorAll("button button, [tabindex]:not(button)")).toHaveLength(0);
      // One chevron; the quota summary has none of its own.
      expect(header(name).querySelectorAll("[data-slot=disclosure]")).toHaveLength(1);
      expect(fold.querySelectorAll("[data-slot=disclosure]")).toHaveLength(1);
      expect(header(name).textContent).not.toMatch(/[▲▼▶▷△▽▸▾]/);
      // A thin chevron: 16px, a stroke of 1.5, no fill.
      const mark = fold.querySelector("[data-slot=disclosure]")!;
      expect(mark.getAttribute("class")).toContain("lucide-chevron-right");
      expect(mark.getAttribute("width")).toBe("16");
      expect(mark.getAttribute("stroke-width")).toBe("1.5");
      expect(mark.getAttribute("fill")).toBe("none");
      // The chevron is a target of at least 28px (32px, size-8).
      expect(fold.className).toContain("size-8");
    }
  });

  it("opens the account from anywhere in the row but the chevron, and folds the models from the chevron alone", async () => {
    const user = userEvent.setup();
    await openHarness(fixture({ harnesses: [harness({ harness: "claude", status: "ready" })] }), "#buddies", false);
    const fold = foldHandle("Claude Code"), account = harnessButton("Claude Code");
    expect([fold.getAttribute("aria-expanded"), account.getAttribute("aria-expanded")]).toEqual(["true", "false"]);
    // The name, the provider and the quota summary are all part of the row's button.
    await user.click(within(account).getByText("Claude Code"));
    expect([fold.getAttribute("aria-expanded"), account.getAttribute("aria-expanded")]).toEqual(["true", "true"]);
    expect(screen.getByRole("group", { name: "Claude Code 设置" })).toBeTruthy();
    await user.click(within(account).getByText(/^—$|%$|余额/));
    expect(account.getAttribute("aria-expanded")).toBe("false");
    await user.click(fold);
    expect([fold.getAttribute("aria-expanded"), account.getAttribute("aria-expanded")]).toEqual(["false", "false"]);
    await user.click(account);
    expect(screen.getByRole("group", { name: "Claude Code 设置" })).toBeTruthy();
    await user.click(fold);
    expect([fold.getAttribute("aria-expanded"), account.getAttribute("aria-expanded")]).toEqual(["true", "true"]);
    expect(screen.getByRole("group", { name: "Claude Code 设置" })).toBeTruthy();
  });

  it("works from the keyboard: the chevron first, then the row, each by Enter and by Space", async () => {
    const user = userEvent.setup();
    await openHarness(fixture(), "#buddies", false);
    const fold = foldHandle("Codex"), account = harnessButton("Codex");
    fold.focus();
    await user.keyboard("{Enter}");
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    await user.keyboard(" ");
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    await user.tab();
    expect(document.activeElement).toBe(account);
    await user.keyboard("{Enter}");
    expect(account.getAttribute("aria-expanded")).toBe("true");
    await user.keyboard(" ");
    expect(account.getAttribute("aria-expanded")).toBe("false");
    expect(fold.getAttribute("aria-expanded")).toBe("false");
  });

  it("has a row to click even for a harness whose models are folded away and whose account says nothing", async () => {
    const user = userEvent.setup();
    await openHarness(fixture({ harnesses: [harness({ harness: "zcode", status: "missing" })] }), "#buddies", false);
    expect(foldHandle("ZCode").getAttribute("aria-expanded")).toBe("false");
    await user.click(harnessButton("ZCode"));
    expect(harnessButton("ZCode").getAttribute("aria-expanded")).toBe("true");
    expect(foldHandle("ZCode").getAttribute("aria-expanded")).toBe("false");
  });
});

describe("what opens and closes", () => {
  it("is a Collapsible each: the models of a section, its account and a model's drawer", async () => {
    const user = userEvent.setup();
    await openHarness(fixture({ harnesses: [harness({ harness: "claude", status: "ready" })] }), "#buddies", false);
    const contents = (scope: HTMLElement) => [...scope.querySelectorAll<HTMLElement>("[data-slot=collapsible-content]")];
    const section = harnessSection("Claude Code");
    expect(section.getAttribute("data-state")).toBe("open");
    // Closed: nothing of the account is drawn, and the models are.
    expect(section.querySelector("[data-slot=harness-details]")).toBeNull();
    await user.click(harnessButton("Claude Code"));
    const details = section.querySelector<HTMLElement>("[data-slot=harness-details]")!;
    expect(details.closest("[data-slot=collapsible-content]")!.getAttribute("data-state")).toBe("open");
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
    await openHarness(fixture({ harnesses: [harness({ harness: "claude", status: "ready" })] }), "#buddies", false);
    const identity = "claude / anthropic / claude-sonnet-5";
    await selectModel(user, identity, "Claude Code");
    await user.click(effortCell("medium"));
    const row = modelButton(identity);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(row.querySelector("[data-slot=disclosure]")!.getAttribute("data-state")).toBe("open");
    await user.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("false");
    expect(row.querySelector("[data-slot=disclosure]")!.getAttribute("data-state")).toBe("closed");
    expect(catalogue().querySelector("[data-slot=model-drawer]")).toBeNull();
  });
});

describe("the controls band", () => {
  it("keeps the search apart and the rest in one group whose two buttons never part", async () => {
    await openHarness(fixture(), "#buddies", false);
    const controls = document.querySelector<HTMLElement>("[data-slot=catalogue-controls]")!;
    const group = controls.querySelector<HTMLElement>("[data-slot=catalogue-group]")!;
    const search = screen.getByRole("searchbox", { name: "搜索 buddy" });
    expect(search.parentElement).toBe(controls);
    expect(group.contains(search)).toBe(false);
    expect(group.parentElement).toBe(controls);
    expect([...group.children].map(child => child.getAttribute("data-slot") ?? child.getAttribute("role"))).toEqual(["catalogue-filters", "status", "catalogue-actions"]);
    const actions = group.querySelector<HTMLElement>("[data-slot=catalogue-actions]")!;
    expect(within(actions).getAllByRole("button").map(button => button.textContent)).toEqual(["全部检测", "发现模型"]);
    // From 640px the group and the pair are rows of their own; below it they dissolve into the grid.
    expect(controls.className).toContain("sm:flex-wrap");
    expect(group.className).toContain("sm:flex-wrap");
    expect(actions.className).not.toContain("flex-wrap");
    expect(actions.className).toContain("sm:flex");
  });
});
