import { ReasonMessage } from "./i18n/ReasonMessage";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import i18n from "i18next";
import { App } from "./App";
import { ApiError, createApi } from "./api";
import { BuddyName, BuddyNamesProvider } from "./components/AliasTag";
import { snapshot, fixture, MEDIUM_ID, MEDIUM } from "./buddies/roles.testing";
import { makeDraft, publication, rebaseDraft, draftDiffers } from "./draft";
import { blockingIssues } from "./policy";
import { modelServiceSearchText } from "./buddy-display";
import { buddyQuad, routerCandidates } from "./buddies/roles";
import { reasonText } from "./i18n/reason";
import type { Reason } from "./reason";

const model = "claude / anthropic / claude-sonnet-5";
afterEach(async () => { cleanup(); window.location.hash = ""; await i18n.changeLanguage("zh"); });

it("publishes setting and clearing an alias through the drawer without republishing enabled", async () => {
  const state = snapshot();
  const f = fixture(state);
  f.command.mockImplementation(async (_operation, params) => {
    for (const patch of params.buddySettings ?? []) Object.assign(state.buddies.find(buddy => buddy.buddyId === patch.buddyId)!, patch);
    return { published: true };
  });
  window.location.hash = `#buddies/catalog/buddy/${encodeURIComponent(MEDIUM_ID)}`;
  const user = userEvent.setup();
  render(<App suppliedApi={f.api} />);
  const input = await screen.findByRole("textbox", { name: "别名" });
  expect(document.querySelectorAll('[data-slot=alias-tag]')).toHaveLength(0);
  await user.type(input, "Daily reviewer");
  expect(document.querySelectorAll('[data-slot=alias-tag]')).toHaveLength(0);
  expect(document.querySelector('[data-slot=buddy-alias]')?.previousElementSibling?.querySelector('[data-slot=unsaved-mark]')).not.toBeNull();
  await user.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(f.command).toHaveBeenCalledTimes(1));
  expect(f.command.mock.calls[0][1].buddySettings).toEqual([{ buddyId: MEDIUM_ID, alias: "Daily reviewer" }]);
  await waitFor(() => expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull());
  const row = screen.getByRole("button", { name: model });
  expect(row.querySelector('[data-slot=alias-tag]')?.textContent).toBe("Daily reviewer");
  await user.clear(screen.getByRole("textbox", { name: "别名" }));
  await user.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(f.command).toHaveBeenCalledTimes(2));
  expect(f.command.mock.calls[1][1].buddySettings).toEqual([{ buddyId: MEDIUM_ID, alias: null }]);
  await waitFor(() => expect(document.querySelectorAll('[data-slot=alias-tag]')).toHaveLength(0));
});

const reasons: Reason[] = [
  { code: "alias.conflict", params: { conflictingBuddy: { harness: "codex", provider: "custom", model: "gpt-6-sol", effort: "high" } } },
  { code: "alias.reserved", params: {} },
  { code: "alias.invalid", params: { violation: "length" } },
];
for (const language of ["zh", "en"]) for (const reason of reasons) it(`keeps ${reason.code} under the rejected alias in ${language}`, async () => {
  await i18n.changeLanguage(language);
  const f = fixture(snapshot(), { user_policy_publish: () => { throw new ApiError("INVALID_ARGUMENT", "refused", { buddyId: MEDIUM_ID, reason }); } });
  window.location.hash = `#buddies/catalog/buddy/${encodeURIComponent(MEDIUM_ID)}`;
  render(<App suppliedApi={f.api} />);
  const user = userEvent.setup();
  let input = await screen.findByRole("textbox", { name: language === "zh" ? "别名" : "Alias" });
  await user.type(input, "duplicate");
  await user.click(screen.getByRole("link", { name: /^Router/ }));
  await user.click(screen.getByRole("button", { name: language === "zh" ? "保存" : "Save" }));
  const refusal = await screen.findByRole("alert");
  input = await screen.findByRole("textbox", { name: language === "zh" ? "别名" : "Alias" });
  expect(refusal.textContent).toBe(reasonText(reason));
  expect(refusal.closest('[data-slot=buddy-panel]')).toBe(input.closest('[data-slot=buddy-panel]'));
  expect(input.getAttribute("aria-describedby")).toBe(refusal.id);
  expect(input.getAttribute("aria-invalid")).toBe("true");
  expect((input as HTMLInputElement).value).toBe("duplicate");
  await user.type(input, " fixed");
  expect(screen.queryByRole("alert")).toBeNull();
});

it("retains structured refusals through command and conditional-read HTTP errors", async () => {
  const details = { buddyId: MEDIUM_ID, conflictingBuddyId: "opaque-id", reason: reasons[0] };
  const api = createApi("", vi.fn(async () => new Response(JSON.stringify({ ok: false, error: { code: "INVALID_ARGUMENT", message: "refused", details } }), { status: 400 })));
  await expect(api.command("user_policy_publish", {}, "csrf")).rejects.toMatchObject({ code: "INVALID_ARGUMENT", details });
  await expect(api.snapshot()).rejects.toMatchObject({ code: "INVALID_ARGUMENT", details });
});

it("merges alias drafts per field, retains concurrent enabled changes and reports alias conflicts", () => {
  const source = snapshot();
  source.buddies[0].available = false;
  const baseline = makeDraft(source);
  const draft = structuredClone(baseline);
  draft.buddies[0].alias = "Reviewer";
  expect(draftDiffers(baseline, draft)).toBe(true);
  expect(blockingIssues(baseline, draft)).toEqual([]);
  expect(publication(baseline, draft, "cmd").buddySettings).toEqual([{ buddyId: MEDIUM_ID, alias: "Reviewer" }]);
  const fresh = structuredClone(source);
  fresh.buddies[0].enabled = false;
  const merged = rebaseDraft(draft, baseline, fresh);
  expect(merged.conflicts).toEqual([]);
  expect(merged.draft.buddies[0]).toMatchObject({ alias: "Reviewer", enabled: false });
  fresh.buddies[0].alias = "Another reviewer";
  expect(rebaseDraft(draft, baseline, fresh).conflicts).toMatchObject([{ field: "alias", buddyId: MEDIUM_ID, kind: "changed" }]);
});

it("searches aliases and four-part names in both catalogue and Router candidates", () => {
  const buddy = { ...snapshot().buddies[0], alias: "Review desk" };
  expect(modelServiceSearchText(buddy)).toContain("review desk");
  expect(modelServiceSearchText(buddy)).toContain(MEDIUM.toLowerCase());
  expect(routerCandidates([buddy], [], "review", "REVIEW DESK")[0].buddy).toBe(buddy);
  expect(buddyQuad(buddy)).toBe(MEDIUM);
});

it("resolves aliases by identity fields and draws no tag for an unset alias", () => {
  const buddy = { ...snapshot().buddies[0], buddyId: "cleaned-and-hashed-6d3a", alias: "Reviewer" };
  const { rerender } = render(<BuddyNamesProvider buddies={[buddy]}><BuddyName buddy={{ harness: buddy.harness, provider: buddy.provider, model: buddy.model, effort: buddy.effort }}>{MEDIUM}</BuddyName></BuddyNamesProvider>);
  expect(screen.getByText("Reviewer").closest('[data-slot=alias-tag]')?.getAttribute("data-slot")).toBe("alias-tag");
  rerender(<BuddyNamesProvider buddies={[{ ...buddy, alias: null }]}><BuddyName buddy={buddy}>{MEDIUM}</BuddyName></BuddyNamesProvider>);
  expect(document.querySelector('[data-slot=alias-tag]')).toBeNull();
});

it("adds the current alias to buddy identities nested in routing reasons", () => {
  const buddy = { ...snapshot().buddies[0], alias: "Review desk" };
  const identity = { harness: buddy.harness, provider: buddy.provider, model: buddy.model, effort: buddy.effort };
  const { container } = render(<BuddyNamesProvider buddies={[buddy]}><ReasonMessage reason={{ code: "alias.conflict", params: { conflictingBuddy: identity } }} /></BuddyNamesProvider>);
  expect(container.textContent).toBe(`这个别名已被 ${MEDIUM}Review desk 使用。`);
  expect(container.querySelector('[data-slot=alias-tag]')?.textContent).toBe("Review desk");
});

it("uses the saved alias in the page-wide blocked-save explanation", async () => {
  const state = snapshot();
  state.buddies[0].enabled = false;
  state.buddies[0].alias = "Saved desk";
  const f = fixture(state);
  window.location.hash = `#buddies/catalog/buddy/${encodeURIComponent(MEDIUM_ID)}`;
  render(<App suppliedApi={f.api} />);
  const user = userEvent.setup();
  const input = await screen.findByRole("textbox", { name: "别名" });
  await user.clear(input);
  await user.type(input, "Draft desk");
  await user.click(screen.getByRole("switch", { name: "启用 medium" }));
  state.buddies[0].available = false;
  await waitFor(() => expect(document.querySelector('[data-slot=model-drawer] [role=alert]')?.textContent).toContain("无法启用"), { timeout: 4500 });
  await user.click(screen.getByRole("button", { name: "保存" }));
  const explanation = screen.getAllByRole("alert")[0];
  expect(explanation.querySelector('[data-slot=alias-tag]')?.textContent).toBe("Saved desk");
  expect(explanation.textContent).not.toMatch(/[\[\]]/);
  expect(f.command).not.toHaveBeenCalled();
});
