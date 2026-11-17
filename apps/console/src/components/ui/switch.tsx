import * as React from "react"
import { cn } from "cn"
import { Switch as SwitchPrimitive } from "radix-ui"

// On is live; the area that takes the pointer is larger than the track. The
// look follows aria-checked: a tooltip around the switch overwrites its data-state.
function Switch({
  className,
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        "peer relative inline-flex h-7 w-[30px] shrink-0 items-center bg-transparent transition-colors duration-150 motion-reduce:transition-none outline-none before:pointer-events-none before:absolute before:inset-x-0 before:inset-y-1.5 before:rounded-full before:bg-line2 before:transition-colors before:duration-150 before:motion-reduce:transition-none focus-visible:before:ring-[3px] focus-visible:before:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:opacity-50 aria-checked:before:bg-foreground",
        className
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className="pointer-events-none relative z-10 block size-3 translate-x-0.5 rounded-full bg-card transition-transform duration-150 motion-reduce:transition-none data-[state=checked]:translate-x-4"
      />
    </SwitchPrimitive.Root>
  )
}

export { Switch }
