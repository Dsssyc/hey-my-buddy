import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { cn } from "cn";

/** Read an overflowing single line at 40px/s, including inside a button. */
export function ScrollingTitle({ text, as: Tag = "span", className, focusable = false }: {
  text: string; as?: "span" | "h2"; className?: string; focusable?: boolean;
}) {
  const viewport = useRef<HTMLHeadingElement & HTMLSpanElement>(null);
  const content = useRef<HTMLSpanElement>(null);
  const [distance, setDistance] = useState(0);
  useLayoutEffect(() => {
    const measure = () => setDistance(Math.max(0, content.current!.scrollWidth - viewport.current!.clientWidth));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(viewport.current!);
    observer.observe(content.current!);
    return () => observer.disconnect();
  }, [text]);
  return <Tag ref={viewport} data-slot="scrolling-title" data-overflow={distance > 0}
    tabIndex={focusable ? 0 : undefined} className={cn("scrolling-title", className)} style={{
      "--title-distance": `${distance}px`, "--title-duration": `${distance / 40 + 1}s`,
      "--title-travel-end": `${distance / (distance + 40) * 100}%`,
    } as CSSProperties}>
    <span ref={content} data-slot="scrolling-title-text">{text}</span>
  </Tag>;
}
