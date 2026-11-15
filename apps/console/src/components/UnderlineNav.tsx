import { useLayoutEffect, useRef } from "react";
import type { ComponentProps, Ref, RefObject } from "react";

const GLIDE_DURATION_MS = 170;
const ACTIVE_SELECTOR =
  '[aria-current="page"], [role="tab"][data-state="active"], [role="tab"][aria-selected="true"]';

interface InkRect {
  element: HTMLElement | null;
  left: number;
  width: number;
  fromLeft: number;
  fromWidth: number;
  startedAt: number;
  cleanup?: () => void;
}

function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

function measureInk(container: HTMLElement, item: HTMLElement): { left: number; width: number } {
  const containerRect = container.getBoundingClientRect();
  const itemRect = item.getBoundingClientRect();
  const after = window.getComputedStyle(item, "::after");
  const leftInset = Number.parseFloat(after.left) || 0;
  const rightInset = Number.parseFloat(after.right) || 0;
  const width = itemRect.width - leftInset - rightInset;
  if (itemRect.width <= 0 || width <= 0) return { left: 0, width: 0 };
  return {
    left: itemRect.left - containerRect.left + leftInset,
    width,
  };
}

function easeOutQuart(t: number): number {
  const clamped = Math.min(1, Math.max(0, t));
  return 1 - (1 - clamped) ** 4;
}

function clearGlide(element: HTMLElement | null) {
  if (!element) return;
  element.removeAttribute("data-ink-glide");
  element.style.removeProperty("--ink-dx");
  element.style.removeProperty("--ink-sx");
}

function mergeRefs<T>(...refs: Array<Ref<T> | undefined>) {
  return (node: T | null) => {
    for (const ref of refs) {
      if (!ref) continue;
      if (typeof ref === "function") ref(node);
      else (ref as { current: T | null }).current = node;
    }
  };
}

/**
 * Slides the active item's `::after` ink bar between tabs or links via FLIP
 * transform without adding DOM elements or altering computed rest geometry.
 */
export function useInkGlide(containerRef: RefObject<HTMLElement | null>, enabled = true): void {
  const stateRef = useRef<InkRect>({
    element: null,
    left: 0,
    width: 0,
    fromLeft: 0,
    fromWidth: 0,
    startedAt: 0,
  });

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!enabled || !container) return;

    const sync = (animate: boolean) => {
      const prev = stateRef.current;
      const active = container.querySelector<HTMLElement>(ACTIVE_SELECTOR);
      if (!active) {
        prev.cleanup?.();
        clearGlide(prev.element);
        stateRef.current = { element: null, left: 0, width: 0, fromLeft: 0, fromWidth: 0, startedAt: 0 };
        return;
      }

      const next = measureInk(container, active);
      if (next.width <= 0) {
        prev.cleanup?.();
        clearGlide(prev.element);
        stateRef.current = { element: active, left: 0, width: 0, fromLeft: 0, fromWidth: 0, startedAt: 0 };
        return;
      }

      if (animate && prev.element && prev.element !== active && prev.width > 0 && !prefersReducedMotion()) {
        const elapsed = performance.now() - prev.startedAt;
        const inFlight = prev.element.getAttribute("data-ink-glide") === "run" && elapsed < GLIDE_DURATION_MS;
        const progress = inFlight ? easeOutQuart(elapsed / GLIDE_DURATION_MS) : 1;
        const startLeft = inFlight ? prev.fromLeft + (prev.left - prev.fromLeft) * progress : prev.left;
        const startWidth = inFlight ? prev.fromWidth + (prev.width - prev.fromWidth) * progress : prev.width;

        prev.cleanup?.();
        clearGlide(prev.element);

        active.style.setProperty("--ink-dx", `${startLeft - next.left}px`);
        active.style.setProperty("--ink-sx", `${startWidth / next.width}`);
        active.removeAttribute("data-ink-glide");
        void active.offsetWidth;
        active.setAttribute("data-ink-glide", "run");

        const onEnd = () => {
          clearGlide(active);
          if (stateRef.current.element === active) stateRef.current.cleanup = undefined;
        };
        active.addEventListener("animationend", onEnd, { once: true });

        stateRef.current = {
          element: active,
          left: next.left,
          width: next.width,
          fromLeft: startLeft,
          fromWidth: startWidth,
          startedAt: performance.now(),
          cleanup: () => active.removeEventListener("animationend", onEnd),
        };
        return;
      }

      if (prev.element !== active) {
        prev.cleanup?.();
        clearGlide(prev.element);
        clearGlide(active);
      }
      stateRef.current = {
        ...prev,
        element: active,
        left: next.left,
        width: next.width,
      };
    };

    sync(false);

    const observer = new MutationObserver(() => sync(true));
    observer.observe(container, {
      subtree: true,
      attributes: true,
      attributeFilter: ["aria-current", "data-state", "aria-selected"],
    });

    const resizeObserver = typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(() => sync(false))
      : null;
    resizeObserver?.observe(container);

    return () => {
      observer.disconnect();
      resizeObserver?.disconnect();
      stateRef.current.cleanup?.();
      clearGlide(stateRef.current.element);
    };
  }, [containerRef, enabled]);
}

/** Navigation container whose active link underline glides cleanly across switches. */
export function UnderlineNav({ ref, ...props }: ComponentProps<"nav">) {
  const navRef = useRef<HTMLElement | null>(null);
  useInkGlide(navRef);
  return <nav ref={mergeRefs(navRef, ref)} {...props} />;
}
