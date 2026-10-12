import * as React from "react"
import { cn } from "cn"
import { ChevronRightIcon } from "lucide-react"
import { Collapsible as CollapsiblePrimitive } from "radix-ui"

function Collapsible(props: React.ComponentProps<typeof CollapsiblePrimitive.Root>) {
  return <CollapsiblePrimitive.Root data-slot="collapsible" {...props} />
}

function CollapsibleTrigger(props: React.ComponentProps<typeof CollapsiblePrimitive.Trigger>) {
  return <CollapsiblePrimitive.Trigger data-slot="collapsible-trigger" {...props} />
}

// Opening and closing run on the height Radix measures and publishes as
// --radix-collapsible-content-height, for 200ms. With reduced motion the content
// shows and hides at once.
function CollapsibleContent({
  className,
  ...props
}: React.ComponentProps<typeof CollapsiblePrimitive.Content>) {
  return (
    <CollapsiblePrimitive.Content
      data-slot="collapsible-content"
      className={cn(
        "overflow-hidden animation-duration-200 motion-safe:data-[state=closed]:animate-collapsible-up motion-safe:data-[state=open]:animate-collapsible-down",
        className
      )}
      {...props}
    />
  )
}

/** The mark of a disclosure: a thin chevron that points right when closed and turns down when open. */
function Disclosure({ open, className }: { open: boolean; className?: string }) {
  return (
    <ChevronRightIcon
      data-slot="disclosure"
      data-state={open ? "open" : "closed"}
      size={16}
      strokeWidth={1.5}
      aria-hidden="true"
      className={cn("shrink-0 text-ink2 transition-transform duration-200 motion-reduce:transition-none", open && "rotate-90", className)}
    />
  )
}

export { Collapsible, CollapsibleTrigger, CollapsibleContent, Disclosure }
