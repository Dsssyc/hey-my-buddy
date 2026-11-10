import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { BuddyNamesProvider } from "./components/AliasTag";
import { snapshot } from "./buddies/roles.testing";
import { buddyQuad } from "./buddies/roles";
import { buddyLabel } from "./macro-task-display";
import { modelEffort } from "./task-detail/names";
import { makeDraft } from "./draft";
import { blockingIssues } from "./policy";
import { ReasonMessage } from "./i18n/ReasonMessage";
import { BasisFold } from "./task-detail/facts";
import { NO_BASIS, at } from "./task-detail/task-detail.fixtures";
import { macroTaskTimelineFixture } from "./macro-task-fixtures";
import { SpanReadout, spanReadout } from "./timeline/Tracks";
import { setLanguageChoice } from "./i18n";

const buddy = { ...snapshot().buddies[0], alias: "Review desk" };
afterEach(() => { cleanup(); setLanguageChoice("browser"); });

it("keeps each name's original form independent of its alias", () => {
  const plain = { ...buddy, alias: null };
  expect(buddyQuad(buddy)).toBe(buddyQuad(plain));
  expect(buddyLabel(buddy)).toBe(buddyLabel(plain));
  expect(modelEffort(buddy)).toBe(modelEffort(plain));
  expect(buddyLabel(buddy)).toBe(`${buddy.model} · ${buddy.effort}`);
  expect(modelEffort(buddy)).toBe(`${buddy.model} / ${buddy.effort}`);
});

for (const language of ["zh", "en"] as const) it(`renders buddy values in program sentences as tags in ${language}`, () => {
  setLanguageChoice(language);
  const { container } = render(<BuddyNamesProvider buddies={[buddy]}><ReasonMessage reason={{ code: "alias.conflict", params: { conflictingBuddy: buddy } }} /></BuddyNamesProvider>);
  expect(container.textContent).not.toMatch(/[\[\]]/);
  expect(container.querySelector('[data-slot=alias-tag]')?.textContent).toBe(buddy.alias);
  expect(container.textContent).toContain(buddyQuad(buddy));
  expect(container.textContent).toContain(language === "zh" ? "这个别名已被" : "This alias is already used by");
});

it("renders a policy refusal with the same model and effort and a separate tag", () => {
  const baseline = makeDraft(snapshot());
  baseline.buddies[0] = { ...buddy, enabled: false, available: false };
  const draft = structuredClone(baseline);
  draft.buddies[0].enabled = true;
  const issue = blockingIssues(baseline, draft)[0];
  const { container } = render(<BuddyNamesProvider buddies={draft.buddies}>{issue.message}</BuddyNamesProvider>);
  expect(container.textContent).toContain(`${buddy.model} · ${buddy.effort}`);
  expect(container.textContent).not.toContain(`${buddy.harness} / ${buddy.provider}`);
  expect(container.textContent).not.toMatch(/[\[\]]/);
  expect(container.querySelector('[data-slot=alias-tag]')?.textContent).toBe(buddy.alias);
});

it("uses tags inside constraints, excluded and cooling lists and recorded preferences", async () => {
  const basis = { ...NO_BASIS, constraints: buddy,
    excluded: [{ buddy, note: "recorded" }], cooling: [{ buddy, coolingUntil: at("11:00") }],
    preferences: [{ buddyId: buddy.buddyId, mode: "pin" as const, note: "recorded" }] };
  const { container } = render(<BuddyNamesProvider buddies={[buddy]}><BasisFold basis={basis} /></BuddyNamesProvider>);
  await userEvent.setup().click(screen.getByRole("button", { name: "提交时的约束" }));
  expect(container.querySelectorAll('[data-slot=alias-tag]')).toHaveLength(4);
  expect(container.textContent).not.toMatch(/[\[\]]/);
  expect(container.textContent).toContain("recorded");
});

it("keeps visible readout names stable while accessibility text can carry bracketed aliases", () => {
  const recorded = macroTaskTimelineFixture();
  const span = { ...recorded.spans.find(span => span.kind === "execution")!, buddy };
  const nowMs = Date.parse(recorded.observedAt);
  const plain = spanReadout({ ...span, buddy: { ...buddy, alias: null } }, nowMs);
  expect(spanReadout(span, nowMs).replaceAll(` [${buddy.alias}]`, "")).toBe(plain);
  const { container } = render(<BuddyNamesProvider buddies={[buddy]}><SpanReadout span={span} nowMs={nowMs} /></BuddyNamesProvider>);
  expect(container.textContent).not.toMatch(/[\[\]]/);
  expect(container.textContent).toContain(`${buddy.model} · ${buddy.effort}`);
  expect(container.querySelectorAll('[data-slot=alias-tag]')).toHaveLength(2);
});

it("retains the recorded id form of a reason's buddyId parameter", () => {
  const { container } = render(<BuddyNamesProvider buddies={[buddy]}><ReasonMessage reason={{ code: "router-skip-window", params: { buddyId: buddy.buddyId, retryAt: at("11:00") } }} /></BuddyNamesProvider>);
  expect(container.textContent).toContain(buddy.buddyId);
  expect(container.textContent).not.toContain(buddyQuad(buddy));
  expect(container.textContent).not.toMatch(/[\[\]]/);
  expect(container.querySelector('[data-slot=alias-tag]')?.textContent).toBe(buddy.alias);
});
