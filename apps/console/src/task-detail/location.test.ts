import { describe, expect, it } from "vitest";
import { TASKS_ROOT, macroTaskKey, tasksHash, tasksLocation } from "./location";

describe("the address of the tasks page", () => {
  it("names a macro task, one of its micro tasks and a step of it, each part encoded", () => {
    expect(tasksHash(TASKS_ROOT)).toBe("#tasks");
    expect(tasksHash({ macroTaskId: "macro-1", runId: null, step: null })).toBe("#tasks/macro-1");
    expect(tasksHash({ macroTaskId: "macro-1", runId: "run-9", step: null })).toBe("#tasks/macro-1/run-9");
    expect(tasksHash({ macroTaskId: "macro-1", runId: "run-9", step: 3 })).toBe("#tasks/macro-1/run-9/3");
    // A standalone micro task is its own group, named `run:<id>`; the colon is encoded.
    expect(tasksHash({ macroTaskId: "run:r 1/2", runId: "a/b", step: 2 })).toBe("#tasks/run%3Ar%201%2F2/a%2Fb/2");
  });

  it("reads back what it wrote", () => {
    for (const location of [TASKS_ROOT, { macroTaskId: "macro-1", runId: null, step: null }, { macroTaskId: "run:r 1/2", runId: "a/b", step: 12 }]) {
      expect(tasksLocation(tasksHash(location))).toEqual(location);
    }
  });

  it("names nothing for another page, and ignores a step that is not a positive number", () => {
    expect(tasksLocation("#buddies/catalog")).toBeNull();
    expect(tasksLocation("")).toBeNull();
    expect(tasksLocation("#tasks")).toEqual(TASKS_ROOT);
    for (const step of ["0", "-1", "x", "1.5", "01", "10000"]) expect(tasksLocation(`#tasks/macro-1/run-9/${step}`)).toEqual({ macroTaskId: "macro-1", runId: "run-9", step: null });
    // A micro task without its macro task is not an address.
    expect(tasksLocation("#tasks//run-9/2")).toEqual({ macroTaskId: null, runId: null, step: null });
    // A part that cannot be decoded names nothing.
    expect(tasksLocation("#tasks/%E0%A4%A")).toEqual(TASKS_ROOT);
  });

  it("names the records view and its selected record independently of macro tasks", () => {
    const root = { ...TASKS_ROOT, records: true as const };
    expect(tasksHash(root)).toBe("#tasks/records");
    expect(tasksLocation("#tasks/records")).toEqual(root);
    expect(tasksHash({ ...root, runId: "route/a b" })).toBe("#tasks/records/route%2Fa%20b");
    expect(tasksLocation("#tasks/records/route%2Fa%20b")).toEqual({ ...root, runId: "route/a b" });
  });

  it("takes the macro task of a micro task from its record, else its own standalone group", () => {
    expect(macroTaskKey({ workflow: { macroTaskId: "macro-7" }, lineage: { rootRunId: "root" } })).toBe("macro-7");
    expect(macroTaskKey({ workflow: { macroTaskId: null }, lineage: { rootRunId: "root" } })).toBe("run:root");
    expect(macroTaskKey({ lineage: { rootRunId: "root" } })).toBe("run:root");
    expect(macroTaskKey({ lineage: { rootRunId: null } })).toBeNull();
  });
});
