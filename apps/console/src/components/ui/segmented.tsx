import * as React from "react"
import { cn } from "cn"
import { RadioGroup as RadioGroupPrimitive } from "radix-ui"

// One choice out of a few, side by side: a radio group drawn as joined segments.
// The checked look follows aria-checked: a tooltip around an item overwrites its data-state.
function Segmented({
  className,
  ...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Root>) {
  return (
    <RadioGroupPrimitive.Root
      data-slot="segmented"
      className={cn("inline-flex h-8 w-fit overflow-hidden rounded-md border border-line2", className)}
      {...props}
    />
  )
}

function SegmentedItem({
  className,
  ...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Item>) {
  return (
    <RadioGroupPrimitive.Item
      data-slot="segmented-item"
      className={cn(
        "inline-flex h-full items-center justify-center border-l border-line2 px-3 text-sm whitespace-nowrap text-ink2 outline-none transition-colors duration-150 first:border-l-0 hover:text-foreground motion-reduce:transition-none focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:opacity-50 aria-checked:bg-foreground aria-checked:text-card",
        className
      )}
      {...props}
    />
  )
}

export { Segmented, SegmentedItem }
