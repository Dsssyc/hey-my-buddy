import { describe, expect, it } from "vitest";
import { makeDraft, routerListEqual } from "../draft";
import zh from "../i18n/zh/buddies";
import { routerRefusal } from "../policy";
import type { Draft, RouterHealthEntry, RoutingMode } from "../types";
import {
  FLASH, FLASH_ID, GLM_ID, HIGH, HIGH_ID, MEDIUM, MEDIUM_ID, PENDING_ID, SOL_ID, health, buddies, snapshot,
} from "./roles.testing";
import {
  addRouter, buddyQuad, currentRouterId, healthSummary, moveRouter, recordCounts, removeRouter, routerCandidates, routerLines, routerNote, routerRefusalOf,
} from "./roles";

function entry(buddyId: string, extra: Partial<RouterHealthEntry> = {}): RouterHealthEntry {
  const [harness, provider, model, effort] = buddyId.split(":");
  return { buddyId, index: 0, identity: { harness, provider, model, effort }, eligible: true, ...extra };
}
const draftOf = (...routers: string[]): Draft => makeDraft(snapshot({ routers }));
const listOf = (draft: Draft) => draft.configuration!.routerBuddyIds;

describe("the warning of the recorded window", () => {
  it("is none without health facts, while the window is empty and while nothing fails", () => {
    expect(healthSummary(undefined).warning).toBeNull();
    expect(healthSummary(health({ sampleCount: 0, lastSuccessAt: null })).warning).toBeNull();
    expect(healthSummary(health()).warning).toBeNull();
  });

  it("names the unavailable Router with the recorded code, and a window that keeps failing", () => {
    expect(healthSummary(health({ available: false, reason: { code: "router-skip-window", params: {} } })).warning).toEqual({ kind: "unavailable", reason: { code: "router-skip-window", params: {} } });
    expect(healthSummary(health({ available: false })).warning).toEqual({ kind: "unavailable", reason: null });
    expect(healthSummary(health({ failureCount: 4, consecutiveFailures: 4 })).warning).toEqual({ kind: "failing", count: 4 });
  });

  it("takes the service's availability as it is and derives no failure threshold", () => {
    const summary = healthSummary(health({ available: true, consecutiveFailures: 4, failureCount: 4 }));
    expect(summary.warning).toEqual({ kind: "failing", count: 4 });
  });

  it("does not count abstentions or cancellations as failures", () => {
    expect(healthSummary(health({ abstentionCount: 3, cancelledCount: 2,})).warning).toBeNull();
  });
});

describe("who holds the role", () => {
  it("is the buddy the service names, even when it is not the head of the list", () => {
    expect(currentRouterId([MEDIUM_ID, FLASH_ID], health({ currentRouterBuddyId: FLASH_ID }))).toBe(FLASH_ID);
  });

  it("is nobody when the service names nobody, whatever the list says", () => {
    expect(currentRouterId([MEDIUM_ID], health({ currentRouterBuddyId: null, available: false }))).toBeNull();
  });

  it("is the head of the list only while the snapshot carries no such fact", () => {
    expect(currentRouterId([MEDIUM_ID, FLASH_ID], undefined)).toBe(MEDIUM_ID);
    expect(currentRouterId([MEDIUM_ID, FLASH_ID], health())).toBe(MEDIUM_ID);
    expect(currentRouterId([], undefined)).toBeNull();
  });
});

describe("where each buddy of the list stands", () => {
  const list = [MEDIUM_ID, FLASH_ID, SOL_ID, HIGH_ID];
  const statuses = (recorded: string[], facts: ReturnType<typeof health> | undefined) =>
    routerLines(list, recorded, buddies(), facts).map(({ status }) => status);

  it("says current, standby, waiting to retry with the time, and unavailable with the recorded code", () => {
    const facts = health({
      currentRouterBuddyId: MEDIUM_ID,
      routers: [
        entry(MEDIUM_ID), entry(FLASH_ID),
        entry(SOL_ID, { eligible: false, code: "router-not-eligible" }),
        entry(HIGH_ID, { eligible: false, code: "router-skip-window", inSkipWindow: true, retryAt: "2026-09-27T08:00:00.000Z" }),
      ],
    });
    expect(statuses(list, facts)).toEqual([
      { kind: "current" }, { kind: "standby" },
      { kind: "unavailable", code: "router-not-eligible" },
      { kind: "retry", at: "2026-09-27T08:00:00.000Z" },
    ]);
  });

  it("writes the first buddy as current and no state on the others while the snapshot carries no health facts", () => {
    expect(statuses(list, undefined)).toEqual([{ kind: "current" }, null, null, null]);
  });

  it("writes no state on a buddy that is only in the draft, since nothing is recorded of it", () => {
    expect(statuses([FLASH_ID], undefined)).toEqual([null, null, null, null]);
    const facts = health({ currentRouterBuddyId: MEDIUM_ID, routers: [entry(MEDIUM_ID)] });
    expect(statuses([MEDIUM_ID], facts)).toEqual([{ kind: "current" }, null, null, null]);
  });

  it("keeps the facts of a buddy with the buddy when the draft changes the order", () => {
    const facts = health({ currentRouterBuddyId: MEDIUM_ID, routers: [entry(MEDIUM_ID), entry(FLASH_ID, { eligible: false, code: "x" })] });
    const lines = routerLines([FLASH_ID, MEDIUM_ID], [MEDIUM_ID, FLASH_ID], buddies(), facts);
    expect(lines.map(line => [line.buddyId, line.status?.kind])).toEqual([[FLASH_ID, "unavailable"], [MEDIUM_ID, "current"]]);
  });

  it("leaves the buddy out for a buddy the catalogue no longer has", () => {
    const [line] = routerLines(["gone:p:m:e"], ["gone:p:m:e"], buddies(), undefined);
    expect(line.buddy).toBeUndefined();
    expect(line.buddyId).toBe("gone:p:m:e");
  });
});

describe("the buddies that can be added", () => {
  const quads = (list: string[], mode: RoutingMode, query: string) =>
    routerCandidates(buddies(), list, mode, query).map(({ buddy }) => buddyQuad(buddy));

  it("leaves out the buddies already in the list", () => {
    expect(quads([MEDIUM_ID, FLASH_ID], "review", "")).not.toContain(MEDIUM);
    expect(quads([MEDIUM_ID, FLASH_ID], "review", "")).not.toContain(FLASH);
  });

  it("puts the buddies that can serve in the mode first and keeps the order within each group", () => {
    const review = routerCandidates(buddies(), [], "review", "");
    const refused = review.map(({ refusal }) => !!refusal);
    expect(refused.slice(refused.indexOf(true)).every(Boolean)).toBe(true);
    expect(review.filter(({ refusal }) => !refusal).map(({ buddy }) => buddy.buddyId)).toEqual([MEDIUM_ID, PENDING_ID]);
    // Fast routing admits the one that can only route fast.
    expect(routerCandidates(buddies(), [], "fast", "").filter(({ refusal }) => !refusal).map(({ buddy }) => buddy.buddyId))
      .toEqual([MEDIUM_ID, FLASH_ID, PENDING_ID]);
  });

  it("gives the reason code for each that cannot, and the page's words for a code are the words `routerRefusal` gives", () => {
    const by = Object.fromEntries(routerCandidates(buddies(), [], "review", "").map(({ buddy, refusal }) => [buddy.buddyId, refusal]));
    expect(by[SOL_ID]).toBe("review");
    expect(by[HIGH_ID]).toBe("disabled");
    expect(by[GLM_ID]).toBe("unavailable");
    // The policy layer still words its own refusals; the page words them from the code and must not drift from it.
    for (const mode of ["fast", "review"] as const) for (const buddy of buddies()) {
      const code = routerRefusalOf(buddy, mode);
      expect(code ? zh.roles.refusal[code] : null, `${buddy.buddyId} in ${mode}`).toBe(routerRefusal(buddy, mode));
    }
    expect(by[MEDIUM_ID]).toBeNull();
    expect(by[PENDING_ID]).toBeNull();
  });

  it("filters by any part of the four parts, without regard to case and around the slashes", () => {
    expect(quads([], "review", "SONNET")).toEqual([MEDIUM, HIGH]);
    expect(quads([], "review", "deepseek-official / deepseek")).toEqual([FLASH]);
    expect(quads([], "review", "  max ")).toEqual([FLASH]);
    expect(quads([], "review", "no-such-buddy")).toEqual([]);
  });
});

describe("the three changes of the list", () => {
  it("adds a buddy at the end and leaves the order of the others", () => {
    expect(listOf(addRouter(draftOf(MEDIUM_ID, FLASH_ID), SOL_ID))).toEqual([MEDIUM_ID, FLASH_ID, SOL_ID]);
    expect(listOf(addRouter(draftOf(), MEDIUM_ID))).toEqual([MEDIUM_ID]);
  });

  it("removes a buddy and leaves the order of the others", () => {
    expect(listOf(removeRouter(draftOf(MEDIUM_ID, FLASH_ID, SOL_ID), FLASH_ID))).toEqual([MEDIUM_ID, SOL_ID]);
    expect(listOf(removeRouter(draftOf(MEDIUM_ID), MEDIUM_ID))).toEqual([]);
  });

  it("moves a buddy one place and leaves it where it is at either end", () => {
    const draft = draftOf(MEDIUM_ID, FLASH_ID, SOL_ID);
    expect(listOf(moveRouter(draft, FLASH_ID, -1))).toEqual([FLASH_ID, MEDIUM_ID, SOL_ID]);
    expect(listOf(moveRouter(draft, FLASH_ID, 1))).toEqual([MEDIUM_ID, SOL_ID, FLASH_ID]);
    expect(listOf(moveRouter(draft, MEDIUM_ID, -1))).toEqual([MEDIUM_ID, FLASH_ID, SOL_ID]);
    expect(listOf(moveRouter(draft, SOL_ID, 1))).toEqual([MEDIUM_ID, FLASH_ID, SOL_ID]);
  });

  it("never touches the draft it is given, nor anything but the list", () => {
    const draft = draftOf(MEDIUM_ID);
    const next = addRouter(draft, FLASH_ID);
    expect(listOf(draft)).toEqual([MEDIUM_ID]);
    expect(next.configuration).toEqual({ ...draft.configuration, routerBuddyIds: [MEDIUM_ID, FLASH_ID] });
    expect(next.buddies).toBe(draft.buddies);
    expect(routerListEqual(listOf(moveRouter(addRouter(draft, FLASH_ID), FLASH_ID, -1)), [FLASH_ID, MEDIUM_ID])).toBe(true);
  });

  it("changes nothing where the settings cannot be read", () => {
    const draft = { ...draftOf(MEDIUM_ID), configuration: null };
    expect(addRouter(draft, FLASH_ID)).toBe(draft);
    expect(removeRouter(draft, MEDIUM_ID)).toBe(draft);
    expect(moveRouter(draft, MEDIUM_ID, 1)).toBe(draft);
  });
});

describe("the note under the list", () => {
  const source = (routers: string[], mode: "fast" | "review" = "review") => {
    const draft = makeDraft(snapshot({ routers, configuration: { defaultRoutingMode: mode } }));
    return { configuration: draft.configuration, buddies: draft.buddies };
  };

  it("is none for a list whose first buddy can serve and whose window is sound", () => {
    expect(routerNote(source([MEDIUM_ID]), health({ currentRouterBuddyId: MEDIUM_ID }))).toBeNull();
    expect(routerNote(source([]), undefined)).toBeNull();
  });

  it("says that nobody can serve when the service names nobody and the list is not empty", () => {
    expect(routerNote(source([MEDIUM_ID]), health({ currentRouterBuddyId: null, available: false, reason: { code: "x", params: {} } }))).toEqual({ kind: "nobody" });
    expect(routerNote(source([]), health({ currentRouterBuddyId: null, available: false }))).toEqual({ kind: "unavailable", reason: null });
  });

  it("names the first buddy and why the default mode refuses it, by the reason code", () => {
    expect(routerNote(source([SOL_ID, MEDIUM_ID]), health({ currentRouterBuddyId: MEDIUM_ID })))
      .toEqual({ kind: "head", buddyId: SOL_ID, refusal: "review" });
    expect(routerNote(source([SOL_ID, MEDIUM_ID], "fast"), health({ currentRouterBuddyId: MEDIUM_ID })))
      .toEqual({ kind: "head", buddyId: SOL_ID, refusal: "fast" });
  });

  it("has no reason for a first buddy that is gone from the catalogue", () => {
    expect(routerNote(source(["gone:p:m:e"]), health({ currentRouterBuddyId: "gone:p:m:e" })))
      .toEqual({ kind: "head", buddyId: "gone:p:m:e", refusal: null });
  });

  it("comes after nobody-can-serve and the head's refusal, and before nothing", () => {
    const failing = health({ currentRouterBuddyId: MEDIUM_ID, failureCount: 3, consecutiveFailures: 3 });
    expect(routerNote(source([MEDIUM_ID]), failing)).toEqual({ kind: "failing", count: 3 });
    expect(routerNote(source([SOL_ID]), { ...failing, currentRouterBuddyId: SOL_ID })).toMatchObject({ kind: "head" });
  });
});

describe("the counts written beside their names", () => {
  it("leaves out what is zero or not recorded, and keeps the order of the names", () => {
    const counts = recordCounts(health({
      consecutiveFailures: 2, abstentionCount: 0, cancelledCount: 1,
      budgetExhaustedCount: 0, inputChangedCount: 1,
    }));
    expect(counts).toEqual([
      { name: "consecutiveFailures", n: 2 }, { name: "cancelled", n: 1 }, { name: "inputChanged", n: 1 },
    ]);
  });
});
