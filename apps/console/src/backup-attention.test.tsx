import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BackupAttention } from "./BackupAttention";
import type { BackupPreflight } from "./types";

afterEach(cleanup);
const report: BackupPreflight = {
  policy: "attempt-evidence-v1", ok: false, needsAttention: true,
  copied: { count: 42, paths: [], entries: [] },
  skipped: { count: 25, paths: ["attempts/run/attempt/unregistered.json"],
    entries: [{ path: "attempts/run/attempt/unregistered.json", reason: "not-evidence-file" }] },
  rejected: { count: 1, paths: ["attempts/run/attempt/task.txt"],
    entries: [{ path: "attempts/run/attempt/task.txt", reason: "linked-evidence" }] },
};

describe("backup preflight attention", () => {
  it("shows skipped and refused content with paths and actionable reasons", () => {
    render(<BackupAttention report={report} />);
    const notice = screen.getByRole("status", { name: "备份预检提醒" });
    expect(notice.textContent).toContain("将跳过 25 项，拒绝 1 项");
    expect(notice.textContent).toContain("attempts/run/attempt/task.txt");
    expect(notice.textContent).toContain("证据路径是链接或重解析点");
    expect(notice.textContent).toContain("每类最多显示 20 个路径");
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("clears when a fresh snapshot is clean and makes no claim for missing observations", () => {
    const view = render(<BackupAttention report={report} />);
    view.rerender(<BackupAttention report={{ ...report, needsAttention: false, ok: true,
      skipped: { count: 0, paths: [], entries: [] }, rejected: { count: 0, paths: [], entries: [] } }} />);
    expect(screen.queryByRole("status")).toBeNull();
    view.rerender(<BackupAttention />);
    expect(view.container.textContent).toBe("");
  });
});
