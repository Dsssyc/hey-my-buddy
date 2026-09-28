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

function anchorVisible(anchor: HTMLElement | null): anchor is HTMLElement {
  if (!anchor?.isConnected || document.hidden) return false;
  for (let node: HTMLElement | null = anchor; node; node = node.parentElement) {
    if (node.hidden || node.getAttribute("aria-hidden") === "true") return false;
    const style = getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden") return false;
  }
  return true;
}

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
  /** Element id, so a trigger's `aria-controls` can name the dialog. */
  id?: string;
};

/**
 * The shared non-modal popover (0.16 0.4): positioned fixed against the
 * trigger, clamped into the viewport with an 8px margin, flipping above when
 * below has no room. Outside click, Escape and focus leaving the
 * trigger+popover pair close it; Escape returns focus to the trigger while an
 * outside click never steals focus. The popover is `role="dialog"` without
 * aria-modal, and only one shared popover exists at a time — including the
 * Host event popup, which reuses this lifecycle. Viewport resizes and scrolls
 * re-run the same placement so an unchanged-size popover stays anchored to
 * its trigger and inside the viewport.
 */
export function Popover({ anchor, label, onClose, children, width, className, id }: PopoverProps) {
  const root = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const owner = useRef({}).current;
  const placeRef = useRef<() => void>(() => {});
  // Owners may pass a fresh callback each render; the lifecycle listeners and
  // the single-popover claim stay registered once per anchor.
  const onCloseRef = useRef(onClose);
  useLayoutEffect(() => { onCloseRef.current = onClose; });
  const close = useRef(() => onCloseRef.current()).current;
  const visible = anchorVisible(anchor);

  // A portal is outside the hidden tab's DOM subtree. Close it when its
  // trigger disappears, so switching tabs cannot leave a floating menu behind.
  useLayoutEffect(() => {
    if (!visible) close();
  }, [visible, close]);

  useLayoutEffect(() => {
    if (!anchor || !visible) return;
    const place = () => {
      if (!anchorVisible(anchor)) { close(); return; }
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
    placeRef.current = place;
    place();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(place);
    observer.observe(root.current!);
    return () => {
      observer.disconnect();
      placeRef.current = () => {};
    };
  }, [anchor, children, close, visible]);

  // Opening claims the single shared slot; closing (any way) releases it.
  useEffect(() => visible ? claimPopover(owner, close) : undefined, [owner, close, visible]);

  useEffect(() => {
    if (!anchor || !visible) return;
    const observer = new MutationObserver(() => {
      if (!anchorVisible(anchor)) close();
      else placeRef.current();
    });
    for (let node: HTMLElement | null = anchor; node; node = node.parentElement) {
      observer.observe(node, { attributes: true, attributeFilter: ["hidden", "style", "class", "aria-hidden"] });
    }
    const onVisibility = () => { if (!anchorVisible(anchor)) close(); };
    document.addEventListener("visibilitychange", onVisibility);
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", onVisibility); };
  }, [anchor, close, visible]);

  // Outside pointer press closes; the anchor itself toggles through its own
  // button handler, so presses there are left alone.
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (root.current?.contains(target)) return;
      if (anchor?.contains(target)) return;
      close();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [anchor, close]);

  // Escape closes and returns focus to the trigger button.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      close();
      anchor?.focus();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [anchor, close]);

  // Focus leaving the trigger+popover pair closes without moving focus.
  useEffect(() => {
    const onFocusOut = (event: FocusEvent) => {
      const related = event.relatedTarget as HTMLElement | null;
      if (related && root.current?.contains(related)) return;
      if (related && anchor?.contains(related)) return;
      // Let the browser finish moving focus before deciding. A popover that
      // already closed (for example because focus moved into the next one)
      // cancels the pending check so it can never close its successor.
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const active = document.activeElement;
        if (active && root.current?.contains(active)) return;
        if (active && anchor?.contains(active)) return;
        close();
      });
    };
    let frame = 0;
    document.addEventListener("focusout", onFocusOut);
    return () => {
      document.removeEventListener("focusout", onFocusOut);
      window.cancelAnimationFrame(frame);
    };
  }, [anchor, close]);

  // A viewport resize or any scroll replays the same placement; an
  // unchanged-size popover re-clamps instead of losing its position.
  useEffect(() => {
    const reposition = () => placeRef.current();
    window.addEventListener("resize", reposition);
    document.addEventListener("scroll", reposition, true);
    return () => {
      window.removeEventListener("resize", reposition);
      document.removeEventListener("scroll", reposition, true);
    };
  }, []);

  // tabIndex -1: a click on the popover's plain text focuses the dialog itself
  // instead of the body, so it does not count as focus leaving.
  if (!visible) return null;
  return createPortal(<div ref={root} id={id} role="dialog" aria-label={label} tabIndex={-1}
    className={"shared-popover" + (className ? ` ${className}` : "")}
    style={{ left: position ? `${position.left}px` : undefined, top: position ? `${position.top}px` : undefined,
      visibility: position ? "visible" : "hidden", width }}>
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
