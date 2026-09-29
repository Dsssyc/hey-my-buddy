import { useEffect, useRef } from "react";

type RefreshRead = () => Promise<void>;
const readers = new Set<RefreshRead>();

/** A manual refresh waits for an overlapping poll before starting its own read. */
export async function waitForRead(isPending: () => boolean) {
  const deadline = Date.now() + 30_000;
  while (isPending()) {
    if (Date.now() >= deadline) throw new Error("现有读取尚未完成，请稍后重试。");
    await new Promise(resolve => setTimeout(resolve, 50));
  }
}

/** Visible independent reads join the single header action. */
export function useGlobalRefresh(read: RefreshRead, active: boolean) {
  const current = useRef(read);
  current.current = read;
  useEffect(() => {
    if (!active) return;
    const registered: RefreshRead = () => current.current();
    readers.add(registered);
    return () => { readers.delete(registered); };
  }, [active]);
}

export async function refreshVisibleReads() {
  const outcomes = await Promise.allSettled([...readers].map(read => read()));
  const failed = outcomes.find((result): result is PromiseRejectedResult => result.status === "rejected");
  if (failed) throw failed.reason;
}
