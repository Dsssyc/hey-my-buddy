import { useLayoutEffect, useRef } from "react";
import type { RefObject } from "react";

/** Keep the first visible row fixed as an older page is appended. */
export function useRecordAnchor(scroll: RefObject<HTMLDivElement | null>, count: number, loading: boolean) {
  const anchor = useRef<{ runId: string; top: number } | null>(null);
  const viewport = () => scroll.current?.querySelector<HTMLElement>("[data-slot=scroll-area-viewport]");
  const rows = () => [...(scroll.current?.querySelectorAll<HTMLElement>("[data-run-id]") ?? [])];
  useLayoutEffect(() => {
    if (!anchor.current || loading) return;
    const saved = anchor.current;
    const row = rows().find(node => node.dataset.runId === saved.runId);
    const view = viewport();
    if (row && view) view.scrollTop += row.getBoundingClientRect().top - saved.top;
    anchor.current = null;
  }, [count, loading]);
  return () => {
    const view = viewport();
    if (!view) return;
    const row = rows().find(node => node.getBoundingClientRect().bottom > view.getBoundingClientRect().top);
    if (row) anchor.current = { runId: row.dataset.runId!, top: row.getBoundingClientRect().top };
  };
}
