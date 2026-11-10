import * as React from "react"
import { cn } from "cn"
import { Popover as PopoverPrimitive } from "radix-ui"

const RevealContext = React.createContext<{ open: boolean; trigger: React.RefObject<HTMLElement | null> }>(null!)

function Popover({ open: controlledOpen, defaultOpen = false, onOpenChange, ...props }: React.ComponentProps<typeof PopoverPrimitive.Root>) {
  const [uncontrolledOpen, setOpen] = React.useState(defaultOpen)
  const trigger = React.useRef<HTMLElement>(null)
  const open = controlledOpen ?? uncontrolledOpen
  return <RevealContext.Provider value={{ open, trigger }}>
    <PopoverPrimitive.Root data-slot="popover" {...props} open={open} onOpenChange={value => { setOpen(value); onOpenChange?.(value) }} />
  </RevealContext.Provider>
}

function PopoverAnchor(props: React.ComponentProps<typeof PopoverPrimitive.Anchor>) {
  return <PopoverPrimitive.Anchor data-slot="popover-anchor" {...props} />
}

function assignRef<T>(ref: React.Ref<T> | undefined, node: T | null) {
  if (typeof ref === "function") return ref(node)
  if (ref) ref.current = node
}

function PopoverTrigger({ ref, ...props }: React.ComponentProps<typeof PopoverPrimitive.Trigger>) {
  const { trigger } = React.useContext(RevealContext)
  const attach = React.useCallback((node: HTMLButtonElement | null) => {
    trigger.current = node
    return assignRef(ref, node)
  }, [ref, trigger])
  return <PopoverPrimitive.Trigger data-slot="popover-trigger" {...props} ref={attach} />
}

type ContentProps = Omit<React.ComponentProps<typeof PopoverPrimitive.Content>, "side"> & { side?: "top" | "bottom" }

function PopoverContent({ className, align = "center", side = "bottom", sideOffset = 4, ref, ...props }: ContentProps) {
  const { open, trigger } = React.useContext(RevealContext)
  const [content, setContent] = React.useState<HTMLDivElement | null>(null)
  const attach = React.useCallback((node: HTMLDivElement | null) => {
    setContent(node)
    return assignRef(ref, node)
  }, [ref])
  React.useLayoutEffect(() => {
    if (!content) return
    const measure = () => {
      const anchor = trigger.current!.getBoundingClientRect()
      const card = content.getBoundingClientRect()
      const style = getComputedStyle(content)
      // Closing can interrupt an opening; remove its current translation from the measurement.
      const top = content.parentElement!.getBoundingClientRect().top + content.offsetTop
      const bottom = top + card.height
      content.style.setProperty("--reveal-left", `${anchor.left - card.left - parseFloat(style.borderLeftWidth)}px`)
      content.style.setProperty("--reveal-right", `${card.right - anchor.right - parseFloat(style.borderRightWidth)}px`)
      content.style.setProperty("--reveal-start-y", `${content.dataset.side === "top"
        ? anchor.top - bottom + parseFloat(style.borderBottomWidth)
        : anchor.bottom - top - parseFloat(style.borderTopWidth)}px`)
      content.dataset.revealReady = "true"
    }
    if (!open) { measure(); return }
    // Radix resolves placement in its layout pass. Read its final rectangles once on the next frame.
    const frame = requestAnimationFrame(measure)
    return () => cancelAnimationFrame(frame)
  }, [content, open, trigger])
  return <PopoverPrimitive.Portal>
    <PopoverPrimitive.Content data-slot="popover-content" {...props} ref={attach} align={align} side={side} sideOffset={sideOffset}
      className={cn("popover-reveal overlay-surface z-50 w-72 border bg-popover p-4 text-popover-foreground shadow-md outline-hidden", className)} />
  </PopoverPrimitive.Portal>
}

export { Popover, PopoverAnchor, PopoverTrigger, PopoverContent }
