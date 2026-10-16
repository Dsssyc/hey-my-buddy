import { useLayoutEffect, useRef, useState } from "react";

/** A tooltip is useful only when the label loses text in its available width. */
export function useTextClipped<T extends HTMLElement>(text: string) {
  const ref = useRef<T>(null);
  const [clipped, setClipped] = useState(false);
  useLayoutEffect(() => {
    const element = ref.current!;
    const update = () => setClipped(element.scrollWidth > element.clientWidth);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [text]);
  return [ref, clipped] as const;
}
