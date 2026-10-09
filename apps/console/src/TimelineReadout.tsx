import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { Popover } from "./Popover";
import type { TimelineGap, TimelineLayout } from "./objective-timeline-layout";
import { clockTime, durationShort } from "./objective-display";

/** Idle intervals show both complete local instants, including the date across long breaks. */
export function idleReadout(gap: TimelineGap): string {
  const instant = (ms: number) => new Date(ms).toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  return `空闲 ${durationShort(gap.endMs - gap.startMs)} · ${instant(gap.startMs)}–${instant(gap.endMs)}`;
}

/** Native pointer updates touch only these two nodes, never React timeline/row state. */
export function TimelineReadout({ gridRef, scrollRef, scale }: {
  gridRef: RefObject<HTMLDivElement | null>;
  scrollRef: RefObject<HTMLDivElement | null>;
  scale: TimelineLayout & { widthPx: number };
}) {
  const [focusedIdle, setFocusedIdle] = useState<{ anchor: HTMLElement; gap: TimelineGap } | null>(null);
  const suppressedFocus = useRef<HTMLElement | null>(null);
  const focusedGap = scale.gaps.find(gap => gap.id === focusedIdle?.gap.id && gap.collapsed);
  useEffect(() => {
    if (focusedIdle && (!focusedGap || !focusedIdle.anchor.isConnected)) setFocusedIdle(null);
  }, [focusedIdle, focusedGap]);
  const lineRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLOutputElement>(null);
  useEffect(() => {
    const grid = gridRef.current, scroller = scrollRef.current;
    const line = lineRef.current, text = textRef.current;
    const track = line?.parentElement;
    if (!grid || !scroller || !line || !text || !track) return;
    const hide = () => { line.hidden = true; text.hidden = true; };
    const move = (event: PointerEvent) => {
      if (event.pointerType === "touch" || scale.startMs === null || scale.endMs === null) { hide(); return; }
      const box = track.getBoundingClientRect();
      const scrollBox = scroller.getBoundingClientRect();
      // Sticky labels cover the start of a scrolled track. Reading that hidden
      // time would contradict the visible row label beneath the pointer.
      const labelPx = Number.parseFloat(getComputedStyle(scroller).getPropertyValue("--label-w")) || 240;
      const x = event.clientX - box.left;
      if (box.width <= 0 || x < 0 || x > box.width || event.clientX < scrollBox.left + labelPx) { hide(); return; }
      const percent = Math.max(0, Math.min(100, x / box.width * 100));
      const gap = scale.gaps.find(candidate => candidate.collapsed
        && percent >= candidate.fromPercent && percent <= candidate.toPercent);
      let reading: string;
      if (gap) {
        reading = idleReadout(gap);
      } else {
        // Invert the same monotonic mapping used for bars and zoom anchors.
        let lo = scale.startMs, hi = scale.endMs;
        for (let index = 0; index < 44; index += 1) {
          const mid = (lo + hi) / 2;
          if ((scale.position(mid) ?? 0) < percent) lo = mid; else hi = mid;
        }
        reading = clockTime(Math.round((lo + hi) / 2));
      }
      line.style.left = `${percent}%`;
      // Keep text in the visible portion of the scrollport, including when
      // the idle blocks require horizontal scrolling at the fit scale.
      const visibleLeft = Math.max(0, scrollBox.left + labelPx - box.left);
      const visibleRight = Math.min(box.width, scrollBox.right - box.left);
      text.textContent = reading;
      text.style.maxWidth = `${Math.max(0, visibleRight - visibleLeft)}px`;
      text.hidden = false;
      const textWidth = text.getBoundingClientRect().width;
      text.style.left = `${Math.max(visibleLeft, Math.min(x + 6, visibleRight - textWidth))}px`;
      line.hidden = false;
    };
    const onFocus = (event: FocusEvent) => {
      const anchor = (event.target as HTMLElement).closest<HTMLElement>(".idle-block");
      if (anchor === suppressedFocus.current) return;
      const gap = scale.gaps.find(candidate => candidate.id === anchor?.dataset.gapId && candidate.collapsed);
      if (anchor && gap) setFocusedIdle({ anchor, gap });
    };
    hide();
    grid.addEventListener("focusin", onFocus);
    grid.addEventListener("pointermove", move);
    grid.addEventListener("pointerleave", hide);
    scroller.addEventListener("scroll", hide);
    // Geometry is read at each move. A resize hides the stale reading until
    // the next pointer sample, using the existing browser observation API.
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(hide);
    observer?.observe(scroller);
    return () => {
      grid.removeEventListener("focusin", onFocus);
      grid.removeEventListener("pointermove", move);
      grid.removeEventListener("pointerleave", hide);
      scroller.removeEventListener("scroll", hide);
      observer?.disconnect();
      hide();
    };
  }, [gridRef, scrollRef, scale]);
  return <>
    <div ref={lineRef} className="timeline-readout-line" hidden />
    <output ref={textRef} className="timeline-readout" hidden />
    {focusedIdle && focusedGap && <Popover anchor={focusedIdle.anchor} label="空闲区间" width="28em"
      boundary={scrollRef.current?.closest<HTMLElement>(".timeline-view")}
      onClose={() => {
        // Escape returns to the anchor synchronously after onClose. That focus
        // must not reopen the popup; a later deliberate focus may open it again.
        const anchor = focusedIdle.anchor;
        suppressedFocus.current = anchor;
        queueMicrotask(() => { if (suppressedFocus.current === anchor) suppressedFocus.current = null; });
        setFocusedIdle(null);
      }}>{idleReadout(focusedGap)}</Popover>}
  </>;
}
