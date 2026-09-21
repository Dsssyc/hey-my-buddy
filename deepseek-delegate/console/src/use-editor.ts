import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { ApiError, errorText } from "./api";
import { makeDraft, publication } from "./draft";
import type { Draft, Snapshot, WriterGrant } from "./types";

export function useEditor(
  api: ConsoleApi,
  snapshot: Snapshot,
  refresh: () => Promise<Snapshot | null>,
) {
  const [grant, setGrantState] = useState<WriterGrant | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [auxiliaryBusy, setAuxiliaryBusy] = useState(false);
  const [notice, setNotice] = useState(""),
    [uncertain, setUncertain] = useState(false);
  const current = useRef<WriterGrant | null>(null);
  const csrf = useRef(snapshot.csrfToken);
  csrf.current = snapshot.csrfToken;
  const pendingSave = useRef<ReturnType<typeof publication> | null>(null);
  const setGrant = useCallback((value: WriterGrant | null) => {
    current.current = value;
    setGrantState(value);
  }, []);

  useEffect(() => {
    if (
      grant &&
      snapshot.gate.phase === "writing" &&
      snapshot.gate.writer?.writerId === grant.writerId &&
      snapshot.gate.writer.generation === grant.generation &&
      !draft
    )
      setDraft(makeDraft(snapshot));
  }, [snapshot, grant, draft]);

  useEffect(() => {
    if (!grant) return;
    let stopped = false;
    const timer = setInterval(
      async () => {
        const owner = current.current;
        if (!owner) return;
        try {
          const next = await api.command<Partial<WriterGrant>>(
            "evaluation_write_renew",
            {
              writerId: owner.writerId,
              generation: owner.generation,
              writerToken: owner.writerToken,
            },
            csrf.current,
          );
          if (!stopped && current.current?.writerId === owner.writerId)
            setGrant({ ...owner, ...next });
          if (!stopped) await refresh();
        } catch (failure) {
          if (!stopped) {
            setError(errorText(failure));
            if (
              failure instanceof ApiError &&
              [
                "WRITER_NOT_ACTIVE",
                "WRITER_EXPIRED",
                "STALE_GENERATION",
              ].includes(failure.code)
            )
              setGrant(null);
          }
        }
      },
      grant.phase === "writing" ? 20000 : 2000,
    );
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [api, grant?.writerId, grant?.phase, refresh, setGrant]);

  const begin = async () => {
    if (draft && draft.tableRevision !== snapshot.tableRevision) {
      setError(
        "共享表已有新版本。请先复制需要保留的内容，再放弃旧草稿并重新编辑。",
      );
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const next = await api.command<WriterGrant>(
        "evaluation_write_begin",
        {
          requestId: crypto.randomUUID(),
          expectedRevision: snapshot.tableRevision,
          kind: "human",
        },
        snapshot.csrfToken,
      );
      setGrant(next);
      await refresh();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    if (!grant || !draft) return;
    setBusy(true);
    setError("");
    setNotice("");
    // If a reply is lost, retries reuse the same payload and idempotency identity.
    const payload =
      pendingSave.current ?? publication(draft, grant, crypto.randomUUID());
    pendingSave.current = payload;
    try {
      await api.command(
        "evaluation_write_publish",
        payload,
        snapshot.csrfToken,
      );
      pendingSave.current = null;
      setUncertain(false);
      setGrant(null);
      setDraft(null);
      setNotice("已发布新版本。正在执行的任务继续使用原配置。");
      await refresh();
    } catch (failure) {
      const ambiguous =
        failure instanceof ApiError &&
        (failure.code === "NETWORK" || failure.code.startsWith("HTTP_5"));
      setUncertain(ambiguous);
      if (!ambiguous) pendingSave.current = null;
      setError(
        ambiguous
          ? "尚未确认保存结果。再次确认会复用同一请求，不会重复发布。"
          : errorText(failure),
      );
    } finally {
      setBusy(false);
    }
  };
  const discard = async () => {
    const owner = grant;
    setBusy(true);
    setError("");
    try {
      if (owner)
        await api.command(
          "evaluation_write_abort",
          {
            commandId: crypto.randomUUID(),
            writerId: owner.writerId,
            generation: owner.generation,
            writerToken: owner.writerToken,
          },
          snapshot.csrfToken,
        );
      setGrant(null);
      setDraft(null);
      pendingSave.current = null;
      setUncertain(false);
      await refresh();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  };
  const hasAuthority =
    !!draft &&
    !!grant &&
    snapshot.gate.phase === "writing" &&
    snapshot.gate.writer?.writerId === grant.writerId &&
    snapshot.gate.writer.generation === grant.generation &&
    snapshot.tableRevision === draft.tableRevision;
  return {
    grant,
    draft,
    setDraft,
    busy: busy || auxiliaryBusy,
    setAuxiliaryBusy,
    error,
    setError,
    notice,
    uncertain,
    hasAuthority,
    begin,
    save,
    discard,
  };
}
export type Editor = ReturnType<typeof useEditor>;
