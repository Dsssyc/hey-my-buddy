import { useEffect, useState } from "react";

/**
 * The browser's Page Visibility as React state: true only while
 * `document.visibilityState` is `"visible"`. Scheduled board reads gate on
 * this — a hidden page stops reading and stops scheduling, and flipping back
 * re-runs the gating effect, which reads once immediately and resumes the
 * cadence. Real visibility events are the browser's; tests that fake
 * `visibilityState` are simulations, not browser evidence.
 */
export function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}

/**
 * True only when the page is visible right now. Every scheduled read callback
 * also consults this at its own invocation: the visibilitychange cleanup can
 * lag the actual hide (a timer firing inside that React-state gap must not
 * start a GET), and a read that resumed after waiting for an in-flight one
 * must re-check before it goes out. Explicit user actions (button presses,
 * retries, filter changes) never consult this.
 */
export function documentVisibleNow(): boolean {
  return document.visibilityState === "visible";
}
