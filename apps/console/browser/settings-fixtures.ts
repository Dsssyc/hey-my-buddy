import type { Page } from "@playwright/test";

/** The current service's view shapes, with synthetic paths and identities only. */
export function storagePlan() {
  const now = Date.now();
  return {
    planId: "synthetic-settings-plan", createdAt: new Date(now).toISOString(), expiresAt: new Date(now + 600_000).toISOString(),
    categories: [
      { id: "harnesses", label: "Harness sessions", bytes: 4_600_000_000, reclaimableBytes: 3_600_000_000, count: 191, eligibleCount: 153, reasons: ["grace-period"] },
      { id: "workspaces", label: "Managed checkouts", bytes: 4_700_000_000, reclaimableBytes: 0, count: 10, eligibleCount: 0, reasons: ["not-accepted", "unresolved-conflict"] },
      { id: "runtimes", label: "Runtimes", bytes: 85_000_000, reclaimableBytes: 0, count: 50, eligibleCount: 0, reasons: ["runtime-in-use"] },
      { id: "backup", label: "Backup", bytes: 68_000_000, reclaimableBytes: 0, count: 1, eligibleCount: 0, reasons: ["durable-record"] },
      { id: "durable", label: "Board and records", bytes: 433_000_000, reclaimableBytes: 0, count: 24, eligibleCount: 0, reasons: ["durable-record"] },
    ], candidates: [{ id: "synthetic-session", category: "harnesses", path: "/synthetic-preview/sessions/retired", bytes: 3_600_000_000, eligible: true, reasons: [] }], orphanProcesses: [],
  };
}

export async function settingsFixture(page: Page) {
  let access = { requireLogin: true, revision: 2, sessions: [
    { id: "a".repeat(24), current: true, lastSeen: 1_792_080_000 },
    { id: "b".repeat(24), current: false, lastSeen: 1_791_907_200 },
  ] };
  let reminders = { silenceSeconds: 900, revision: 1, updatedAt: "2026-10-16T00:00:00Z" };
  const commands: { operation: string; params: Record<string, unknown> }[] = [];
  await page.route("**/api/console", async route => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({ response, json: { ...data, consoleAccess: access, reminderSettings: reminders } });
  });
  await page.route("**/api/command", async route => {
    const body = route.request().postDataJSON();
    commands.push(body);
    let result;
    switch (body.operation) {
      case "storage_plan": result = storagePlan(); break;
      case "storage_apply": result = { planId: body.params.planId, removedBytes: 3_600_000_000, removed: [{ id: "synthetic-session", path: "/synthetic-preview/sessions/retired", bytes: 3_600_000_000 }], skipped: [], complete: true }; break;
      case "console_access_set": access = { ...access, requireLogin: body.params.requireLogin, revision: access.revision + 1, sessions: body.params.requireLogin ? access.sessions : [] }; result = access; break;
      case "console_session_revoke": access = { ...access, sessions: access.sessions.filter(session => session.id !== body.params.sessionId) }; result = access; break;
      case "console_logout": access = { ...access, sessions: access.sessions.filter(session => !session.current) }; result = access; break;
      case "reminder_settings_set": reminders = { silenceSeconds: body.params.silenceSeconds, revision: reminders.revision + 1, updatedAt: new Date().toISOString() }; result = reminders; break;
      default: return route.fallback();
    }
    await route.fulfill({ json: { ok: true, result } });
  });
  return commands;
}
