import { useCallback, useEffect, useRef, useState } from "react";
import { errorText, isAbortError } from "../api";
import { documentVisibleNow, useDocumentVisible } from "../page-visibility";
import type { Task } from "../types";
import type { TaskDetailClient } from "./client";
import type { Audit, Inquiries, RoutingEntry, Workflow } from "./types";

type Kind = "task" | "workflow" | "audit" | "inquiries";

type Data = {
  task: Task | null;
  workflow: Workflow | null;
  audit: Audit | null;
  routing: RoutingEntry[];
  inquiries: Inquiries | null;
  verifiedAtMs: number | null;
  errors: Record<Kind, string>;
};

const empty: Data = {
  task: null, workflow: null, audit: null, routing: [], inquiries: null, verifiedAtMs: null,
  errors: { task: "", workflow: "", audit: "", inquiries: "" },
};

/** A micro task that is queued or executing changes under the reader's eyes. */
const LIVE_STATUSES = ["queued", "running", "cancelling"];
const POLL_MS = 3000;

export type DetailRead = Omit<Data, "errors"> & {
  /** The first read that failed and has not succeeded since; what was read before stays. */
  error: string;
  live: boolean;
  refresh: () => void;
};

/**
 * Everything the detail of one micro task reads. The task and the compact
 * collaboration record are read when the detail opens and the record every
 * three seconds while the page shows; the audit is read when it opens and when
 * the record's revision moves; while the micro task is live the task and its
 * inquiries are read on the same beat, and otherwise the inquiries once.
 * A failed read keeps what was read, and nothing here writes.
 */
export function useTaskDetail(client: TaskDetailClient, runId: string, active: boolean): DetailRead {
  const [data, setData] = useState<Data>(empty);
  const [round, setRound] = useState(0);
  const visible = useDocumentVisible();
  const run = active && visible;
  const sequence = useRef<Record<Kind, number>>({ task: 0, workflow: 0, audit: 0, inquiries: 0 });
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);

  const load = useCallback(async <T,>(kind: Kind, work: (signal: AbortSignal) => Promise<{ value: T; verifiedAtMs: number | null }>,
    apply: (previous: Data, value: T) => Data) => {
    const signal = lifetime.current!.signal;
    const mine = ++sequence.current[kind];
    try {
      const { value, verifiedAtMs } = await work(signal);
      if (signal.aborted || mine !== sequence.current[kind]) return;
      setData(previous => {
        const next = apply(previous, value);
        return { ...next, verifiedAtMs: verifiedAtMs ?? next.verifiedAtMs, errors: { ...next.errors, [kind]: "" } };
      });
    } catch (failure) {
      if (signal.aborted || mine !== sequence.current[kind] || isAbortError(failure)) return;
      setData(previous => ({ ...previous, errors: { ...previous.errors, [kind]: errorText(failure) } }));
    }
  }, []);

  const loadTask = useCallback(() => load("task", signal => client.task(runId, signal), (previous, task) => ({ ...previous, task })), [client, runId, load]);
  const loadWorkflow = useCallback(() => load("workflow", () => client.workflow(runId), (previous, workflow) => ({ ...previous, workflow })), [client, runId, load]);
  const loadAudit = useCallback(() => load("audit", () => client.audit(runId),
    (previous, value) => ({ ...previous, audit: value.audit, routing: value.routing })), [client, runId, load]);
  const loadInquiries = useCallback(() => load("inquiries", () => client.inquiries(runId), (previous, inquiries) => ({ ...previous, inquiries })), [client, runId, load]);

  const collaboration = data.task === null ? null : !!data.task.workflow;
  const live = data.task !== null && LIVE_STATUSES.includes(data.task.status);
  const revision = data.workflow?.revision ?? null;

  useEffect(() => { if (run) void loadTask(); }, [run, round, loadTask]);

  // The record moving means the task moved with it: its attempt, result and stop.
  const readAt = useRef<number | null>(null);
  useEffect(() => {
    if (revision !== null && readAt.current !== null && readAt.current !== revision && run) void loadTask();
    readAt.current = revision;
  }, [revision, run, loadTask]);

  useEffect(() => {
    if (!run || !collaboration) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      // The visibility cleanup can lag the hide; a timer firing inside that gap starts no read.
      if (!documentVisibleNow()) return;
      await loadWorkflow();
      if (current && documentVisibleNow()) timer = setTimeout(poll, POLL_MS);
    }
    void poll();
    return () => { current = false; clearTimeout(timer); };
  }, [run, round, collaboration, loadWorkflow]);

  useEffect(() => { if (run && collaboration && revision !== null) void loadAudit(); }, [run, round, collaboration, revision, loadAudit]);

  useEffect(() => {
    if (!run || !collaboration) return;
    void loadInquiries();
    if (!live) return;
    const timer = setInterval(() => {
      if (!documentVisibleNow()) return;
      void loadTask();
      void loadInquiries();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [run, round, collaboration, live, loadInquiries, loadTask]);

  const refresh = useCallback(() => setRound(value => value + 1), []);
  const { errors, ...read } = data;
  return { ...read, error: Object.values(errors).find(Boolean) ?? "", live, refresh };
}
