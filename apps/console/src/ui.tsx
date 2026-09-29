import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";

/** The explanation keeps this distance from every viewport edge. */
const HELP_MARGIN_PX = 8;
/** Gap between the `?` button and its explanation, matching the former CSS. */
const HELP_GAP_PX = 6;
/** Upper bound for the explanation's inline size; long text wraps below it. */
const HELP_MAX_WIDTH_PX = 26 * 16;

type HelpPlacement = { left: number; top: number; maxWidth: number };

/**
 * A `?` hover/focus tooltip for explanations that used to be permanent text.
 * The explanation is the button's description, so a screen reader announces it
 * with the button; Escape hides it without moving focus (WCAG 1.4.13).
 *
 * The explanation renders through a portal with `position: fixed`, so a
 * scrolling or `overflow: hidden` ancestor (the Buddy-config columns and
 * window edges in particular) can never clip it. It is centred under the
 * button, shifted left or right while near a viewport edge, flipped above when
 * the space below is too small, and capped in width so long text wraps.
 */
export function Help({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  const anchor = useRef<HTMLButtonElement>(null);
  const tip = useRef<HTMLSpanElement>(null);
  const [active, setActive] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [placement, setPlacement] = useState<HelpPlacement | null>(null);
  const open = active && !dismissed;

  const place = useCallback(() => {
    const button = anchor.current;
    const panel = tip.current;
    if (!button || !panel) return;
    const rect = button.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const maxWidth = Math.max(0, Math.min(HELP_MAX_WIDTH_PX, viewportWidth - HELP_MARGIN_PX * 2));
    const width = Math.min(panelRect.width, maxWidth);
    const height = panelRect.height;
    const left = Math.max(HELP_MARGIN_PX, Math.min(rect.left + rect.width / 2 - width / 2, viewportWidth - width - HELP_MARGIN_PX));
    const below = rect.bottom + HELP_GAP_PX;
    const top = below + height <= viewportHeight - HELP_MARGIN_PX
      ? below
      : Math.max(HELP_MARGIN_PX, rect.top - HELP_GAP_PX - height);
    setPlacement(previous => previous && previous.left === left && previous.top === top && previous.maxWidth === maxWidth
      ? previous : { left, top, maxWidth });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    place();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(place);
    observer.observe(tip.current!);
    return () => observer.disconnect();
  }, [open, place, children]);

  // A viewport resize or any scroll replays the placement, so the fixed
  // explanation stays anchored to its button and inside the viewport.
  useEffect(() => {
    if (!open) return;
    const reposition = () => place();
    window.addEventListener("resize", reposition);
    document.addEventListener("scroll", reposition, true);
    return () => {
      window.removeEventListener("resize", reposition);
      document.removeEventListener("scroll", reposition, true);
    };
  }, [open, place]);

  function close() {
    setActive(false);
    setDismissed(false);
  }

  return <span className="help"
    onMouseEnter={() => setActive(true)} onMouseLeave={close}>
    <button ref={anchor} type="button" className="help-button" aria-label={label} aria-describedby={id}
      onFocus={() => setActive(true)}
      onBlur={close}
      onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); setDismissed(true); } }}>?</button>
    {createPortal(<span ref={tip} role="tooltip" id={id} className="help-tip"
      style={{ left: `${placement?.left ?? 0}px`, top: `${placement?.top ?? 0}px`, maxWidth: placement ? `${placement.maxWidth}px` : undefined,
        visibility: open && placement ? "visible" : "hidden" }}>{children}</span>, document.body)}
  </span>;
}

export function Icon({
  name,
  size = 18,
}: {
  name:
    | "tasks"
    | "models"
    | "settings"
    | "refresh"
    | "arrow"
    | "check"
    | "clock"
    | "sun"
    | "moon";
  size?: number;
}) {
  const paths = {
    tasks: "M4 5h16M4 12h16M4 19h10",
    models: "m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5",
    settings: "M4 7h16M4 17h16M8 4v6M16 14v6",
    refresh:
      "M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.5-2L20 8M4 16l2.4 3A7 7 0 0 0 18 17",
    arrow: "M5 12h14m-6-6 6 6-6 6",
    check: "m5 12 4 4L19 6",
    clock: "M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
    sun: "M12 4V2M12 22v-2M4 12H2M22 12h-2M6.3 6.3 4.9 4.9M19.1 19.1l-1.4-1.4M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
    moon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z",
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "green" | "amber" | "red";
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-mark" aria-hidden="true">
        ＋
      </div>
      <h2>{title}</h2>
      {children != null && children !== "" && <p>{children}</p>}
      {action}
    </div>
  );
}

export function formatDate(value: string | number | null | undefined) {
  if (!value) return "未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "未记录"
    : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(date);
}

export function display(value: unknown) {
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : "未记录";
}

export const statusLabels: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  cancelling: "正在取消",
  completed: "执行完成",
  cancelled: "已取消",
  failed: "执行失败",
  "reconciliation-needed": "等待核对",
  "waiting-assistance": "等待协助",
  "waiting-decision": "等待决定",
  "waiting-host": "等待 Host",
  "awaiting-host": "等待 Host",
  "waiting-helpers": "协助执行中",
  executing: "执行中",
  delivered: "等待验收",
  accepted: "已验收",
  yielded: "回合已结束",
};
export function Status({ status }: { status: string }) {
  return (
    <Badge
      tone={
        status === "running" || status === "completed"
          ? "green"
          : status === "failed"
            ? "red"
            : status === "queued" ||
                status.includes("waiting") ||
                status === "reconciliation-needed"
              ? "amber"
              : "neutral"
      }
    >
      {statusLabels[status] || status}
    </Badge>
  );
}
