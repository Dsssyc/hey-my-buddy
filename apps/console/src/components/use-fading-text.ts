import { useTextClipped } from "./use-text-clipped";

/** Fade a clipped label and offer its full text to the caller's tooltip. */
export function useFadingText(text: string) {
  const [ref, clipped] = useTextClipped<HTMLSpanElement>(text);
  return { ref, className: clipped ? "text-fade" : "truncate", hint: clipped ? text : undefined };
}
