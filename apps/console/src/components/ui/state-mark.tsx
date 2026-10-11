import * as React from "react"
import { cn } from "cn"

/**
 * The fixed marks a state is drawn with (ADR-029 clause 8): a filled circle is
 * what runs or is on, a diamond waits for a hand, a check is accepted, a cross
 * failed, and a hollow circle has not started or is absent. The same state is
 * the same mark in a list, on the timeline and in a detail.
 */
const marks = {
  live: <circle cx="6" cy="6" r="4.5" className="fill-live" />,
  hand: <path d="M6 .8 11.2 6 6 11.2.8 6Z" className="fill-hand-mark" />,
  done: <path d="M2 6.4 4.8 9 10 3.2" fill="none" strokeWidth="1.8" className="stroke-past" />,
  fail: <path d="M2.5 2.5 9.5 9.5M9.5 2.5 2.5 9.5" fill="none" strokeWidth="1.8" className="stroke-fail" />,
  idle: <circle cx="6" cy="6" r="4" fill="none" strokeWidth="1.5" className="stroke-ink3" />,
}

function StateMark({
  kind,
  className,
  ...props
}: Omit<React.ComponentProps<"svg">, "children"> & { kind: keyof typeof marks }) {
  return (
    <svg
      data-slot="state-mark"
      data-kind={kind}
      viewBox="0 0 12 12"
      aria-hidden="true"
      className={cn("size-3 shrink-0 overflow-visible", className)}
      {...props}
    >
      {marks[kind]}
    </svg>
  )
}

/** Something on this control is changed and not saved yet. */
function UnsavedMark({ label, className }: { label: string; className?: string }) {
  return <span data-slot="unsaved-mark" role="img" aria-label={label} className={cn("inline-block size-1.5 shrink-0 rounded-full bg-hand-mark", className)} />
}

export { StateMark, UnsavedMark }
