import { useCallback, useEffect, useRef, useState } from "react";
import { errorText, isAbortError } from "../api";
import { documentVisibleNow, useDocumentVisible } from "../page-visibility";
import type { Read, TaskDetailClient } from "./client";
import type { Audit, Basis, Inquiries, RoutingEntry, TaskView, Workflow } from "./types";

type Kind = "task" | "workflow" | "audit" | "inquiries" | "basis";

type Data = {
  view: TaskView | null;
  workflow: Workflow | null;
  audit: Audit | null;
  routing: RoutingEntry[];
  bases: Record<string, Basis>;
  inquiries: Inquiries | null;
  /** When the server checked the reply each part comes from; null when the reply says nothing of it. */
  verified: Record<Kind, number | null>;
  errors: Record<Kind, string>;
};

const KINDS: Kind[] = ["task", "workflow", "audit", "inquiries", "basis"];
const empty: Data = {
  view: null, workflow: null, audit: null, routing: [], bases: {}, inquiries: null,
  verified: Object.fromEntries(KINDS.map(kind => [kind, null])) as Record<Kind, number | null>,
  errors: Object.fromEntries(KINDS.map(kind => [kind, ""])) as Record<Kind, string>,
};

/** A micro task that is queued or executing changes under the reader's eyes. */
const LIVE_STATUSES = ["queued", "running", "cancelling"];
/** The routing decisions the program made without a Router: the bounds they were made within are read apart. */
const PROGRAM_SOURCES = ["single-candidate", "no-candidate"];
const POLL_MS = 3000;

export type DetailRead = {
  view: TaskView | null;
  workflow: Workflow | null;
  audit: Audit | null;
  routing: RoutingEntry[];
  bases: Record<string, Basis>;
  inquiries: Inquiries | null;
  /** When the server checked the reply the head of the detail rests on. */
  verifiedAtMs: number | null;
  /** The first read that failed and has not succeeded since; what was read before stays. */
  error: string;
  /** A read the reader asked for is on its way. */
  busy: boolean;
  refresh: () => void;
};

/**
 * Everything the detail of one micro task reads. The task and the compact
 * collaboration record are read when the detail opens and the record every
 * three seconds while the page shows; the audit is read when it opens and when
 * the record's revision moves, down to the routing decisions of the timeline;
 * while the micro task is live the task and its inquiries are read on the same
 * beat, and otherwise the inquiries once. `listed` is the revision the list
 * the detail was opened from reads for this run, when it has one: a new one
 * reads the task again. A failed read keeps what was read, and nothing here writes.
 */
export function useTaskDetail(client: TaskDetailClient, runId: string, active: boolean, decisionIds: string[], listed: number | null): DetailRead {
  const [data, setData] = useState<Data>(empty);
  const [busy, setBusy] = useState(false);
  const visible = useDocumentVisible();
  const run = active && visible;
  const sequence = useRef<Record<Kind, number>>({ task: 0, workflow: 0, audit: 0, inquiries: 0, basis: 0 });
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);

  const load = useCallback(async <T,>(kind: Kind, work: (signal: AbortSignal) => Promise<Read<T>>,
    apply: (previous: Data, value: T) => Data) => {
    const signal = lifetime.current!.signal;
    const mine = ++sequence.current[kind];
    try {
      const { value, verifiedAtMs } = await work(signal);
      if (signal.aborted || mine !== sequence.current[kind]) return;
      setData(previous => {
        const next = apply(previous, value);
        return { ...next, verified: { ...next.verified, [kind]: verifiedAtMs }, errors: { ...next.errors, [kind]: "" } };
      });
    } catch (failure) {
      if (signal.aborted || mine !== sequence.current[kind] || isAbortError(failure)) return;
      setData(previous => ({ ...previous, errors: { ...previous.errors, [kind]: errorText(failure) } }));
    }
  }, []);

  const wanted = useRef(decisionIds);
  wanted.current = decisionIds;
  const wantedKey = decisionIds.join(" ");
  const loadTask = useCallback(() => load("task", signal => client.task(runId, signal), (previous, view) => ({ ...previous, view })), [client, runId, load]);
  const loadWorkflow = useCallback(() => load("workflow", () => client.workflow(runId), (previous, workflow) => ({ ...previous, workflow })), [client, runId, load]);
  const loadAudit = useCallback(() => load("audit", () => client.audit(runId, wanted.current),
    (previous, value) => ({ ...previous, audit: value.audit, routing: value.routing })), [client, runId, load]);
  const loadInquiries = useCallback(() => load("inquiries", () => client.inquiries(runId), (previous, inquiries) => ({ ...previous, inquiries })), [client, runId, load]);
  const loadBases = useCallback((ids: string[]) => load("basis", async () => {
    const reads = await Promise.all(ids.map(async id => [id, await client.basis(id)] as const));
    return { value: Object.fromEntries(reads.map(([id, read]) => [id, read.value])), verifiedAtMs: null };
  }, (previous, bases: Record<string, Basis>) => ({ ...previous, bases: { ...previous.bases, ...bases } })), [client, load]);

  const collaboration = data.view === null ? null : data.view.record === null;
  const live = data.view !== null && LIVE_STATUSES.includes(data.view.task.status);
  const revision = data.workflow?.revision ?? null;

  // A record without a collaboration record has no record of its own to watch: the list it was opened from says when it moved.
  useEffect(() => { if (run) void loadTask(); }, [run, listed, loadTask]);

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
  }, [run, collaboration, loadWorkflow]);

  useEffect(() => { if (run && collaboration && revision !== null) void loadAudit(); }, [run, collaboration, revision, wantedKey, loadAudit]);

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
  }, [run, collaboration, live, loadInquiries, loadTask]);

  // The bounds a routing decision was made within are frozen with it: each is read once.
  const programmed = data.routing.filter(entry => entry.source !== null && PROGRAM_SOURCES.includes(entry.source)).map(entry => entry.decisionId);
  const missing = programmed.filter(id => !(id in data.bases));
  const missingKey = missing.join(" ");
  useEffect(() => { if (run && missing.length > 0) void loadBases(missing); }, [run, missingKey, loadBases]);

  const busyRef = useRef(false);
  const refresh = useCallback(() => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    const reads = [loadTask(), ...(collaboration ? [loadWorkflow(), loadAudit(), loadInquiries()] : [])];
    void Promise.all(reads).finally(() => { busyRef.current = false; setBusy(false); });
  }, [collaboration, loadTask, loadWorkflow, loadAudit, loadInquiries]);

  const { errors, verified, ...read } = data;
  return {
    ...read, busy, refresh,
    verifiedAtMs: collaboration ? verified.workflow : verified.task,
    error: Object.values(errors).find(Boolean) ?? "",
  };
}
