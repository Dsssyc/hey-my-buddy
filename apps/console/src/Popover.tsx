import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";

/**
 * Registry of the currently open shared popover: opening one closes the
 * previous one (0.16 0.4 — 同一时刻只开一个浮层). Owners subscribe with their
 * id and a close callback.
 */
type PopoverEntry = { id: object; close: () => void };
const openPopovers = new Set<PopoverEntry>();

function claimPopover(id: object, close: () => void): () => void {
  for (const entry of openPopovers) if (entry.id !== id) entry.close();
  const entry: PopoverEntry = { id, close };
  openPopovers.add(entry);
  return () => { openPopovers.delete(entry); };
}

const VIEWPORT_MARGIN_PX = 8;

export type PopoverProps = {
  /** Anchor trigger button; Esc returns focus to it after closing. */
  anchor: HTMLElement | null;
  /** `aria-label` of the non-modal dialog. */
  label: string;
  onClose: () => void;
  children: ReactNode;
  /** Inline size of the floating panel; clamped to the viewport. */
  width?: string;
  /** Extra class for the panel. */
  className?: string;
};

/**
 * The shared non-modal popover (0.16 0.4): positioned fixed against the
 * trigger, clamped into the viewport with an 8px margin, flipping above when
 * below has no room. Outside click, Escape and focus leaving the
 * trigger+popover pair close it; Escape returns focus to the trigger while an
 * outside click never steals focus. The popover is `role="dialog"` without
 * aria-modal, and only one shared popover exists at a time.
 */
export function Popover({ anchor, label, onClose, children, width, className }: PopoverProps) {
  const root = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const owner = useRef({}).current;

  useLayoutEffect(() => {
    if (!anchor) return;
    const place = () => {
      const panel = root.current;
      if (!panel) return;
      const rect = anchor.getBoundingClientRect();
      const panelRect = panel.getBoundingClientRect();
      const maxLeft = window.innerWidth - panelRect.width - VIEWPORT_MARGIN_PX;
      const left = Math.max(VIEWPORT_MARGIN_PX, Math.min(rect.left, maxLeft));
      const below = rect.bottom + panelRect.height + 4;
      const top = below <= window.innerHeight - VIEWPORT_MARGIN_PX
        ? rect.bottom + 4
        : Math.max(VIEWPORT_MARGIN_PX, rect.top - panelRect.height - 4);
      setPosition(previous => previous && previous.left === left && previous.top === top
        ? previous : { left, top });
    };
    place();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(place);
    observer.observe(root.current!);
    return () => observer.disconnect();
  }, [anchor, children]);

  // Opening claims the single shared slot; closing (any way) releases it.
  useEffect(() => claimPopover(owner, onClose), [owner, onClose]);

  // Outside pointer press closes; the anchor itself toggles through its own
  // button handler, so presses there are left alone.
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (root.current?.contains(target)) return;
      if (anchor?.contains(target)) return;
      onClose();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [anchor, onClose]);

  // Escape closes and returns focus to the trigger button.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      onClose();
      anchor?.focus();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [anchor, onClose]);

  // Focus leaving the trigger+popover pair closes without moving focus.
  useEffect(() => {
    const onFocusOut = (event: FocusEvent) => {
      const related = event.relatedTarget as HTMLElement | null;
      if (related && root.current?.contains(related)) return;
      if (related && anchor?.contains(related)) return;
      // Let the browser finish moving focus before deciding.
      window.requestAnimationFrame(() => {
        const active = document.activeElement;
        if (active && root.current?.contains(active)) return;
        if (active && anchor?.contains(active)) return;
        onClose();
      });
    };
    document.addEventListener("focusout", onFocusOut);
    return () => document.removeEventListener("focusout", onFocusOut);
  }, [anchor, onClose]);

  // A viewport resize re-clamps through the layout effect's measurement loop.
  useEffect(() => {
    const onResize = () => setPosition(null);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return createPortal(<div ref={root} role="dialog" aria-label={label}
    className={"shared-popover" + (className ? ` ${className}` : "")}
    style={{ left: position ? `${position.left}px` : undefined, top: position ? `${position.top}px` : undefined, width }}>
    {children}
  </div>, document.body);
}

/** Small helper for popover trigger buttons' shared aria wiring. */
export function popoverButtonProps(id: string, open: boolean) {
  return {
    "aria-expanded": open,
    "aria-controls": id,
  } as const;
}
