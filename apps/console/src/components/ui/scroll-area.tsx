import * as React from "react"
import { cn } from "cn"
import { ScrollArea as ScrollAreaPrimitive } from "radix-ui"

function ScrollArea({
  className,
  children,
  viewportProps,
  orientation = "vertical",
  reserveScrollbarGutter = false,
  type = "auto",
  scrollHideDelay = 600,
  onScrollCapture,
  ...props
}: React.ComponentProps<typeof ScrollAreaPrimitive.Root> & {
  viewportProps?: Omit<React.ComponentProps<typeof ScrollAreaPrimitive.Viewport>, "className"> & { className?: string } & Record<`data-${string}`, string | undefined>
  /** "both" adds the horizontal scrollbar beside the vertical one. */
  orientation?: "vertical" | "both"
  /** Keep the custom vertical scrollbar outside the viewport's measured width. */
  reserveScrollbarGutter?: boolean
}) {
  const { className: viewportClassName, ...viewportRest } = viewportProps ?? {}
  const [scrolling, setScrolling] = React.useState(false)
  const hideTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  React.useEffect(() => () => { if (hideTimer.current !== null) clearTimeout(hideTimer.current) }, [])
  const recordScroll: React.UIEventHandler<HTMLDivElement> = event => {
    onScrollCapture?.(event)
    setScrolling(true)
    if (hideTimer.current !== null) clearTimeout(hideTimer.current)
    hideTimer.current = setTimeout(() => setScrolling(false), scrollHideDelay)
  }
  return (
    <ScrollAreaPrimitive.Root
      data-slot="scroll-area"
      data-scrolling={scrolling}
      type={type}
      scrollHideDelay={scrollHideDelay}
      onScrollCapture={recordScroll}
      className={cn("group/scroll-area relative [--scrollbar-size:--spacing(2.5)]", reserveScrollbarGutter && "pr-(--scrollbar-size)", className)}
      {...props}
    >
      <ScrollAreaPrimitive.Viewport
        data-slot="scroll-area-viewport"
        className={cn(
          "size-full rounded-[inherit] transition-[color,box-shadow] duration-150 motion-reduce:transition-none outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-1",
          viewportClassName
        )}
        {...viewportRest}
      >
        {children}
      </ScrollAreaPrimitive.Viewport>
      <ScrollBar />
      {orientation === "both" && <ScrollBar orientation="horizontal" />}
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  )
}

function ScrollBar({
  className,
  orientation = "vertical",
  ...props
}: React.ComponentProps<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>) {
  return (
    <ScrollAreaPrimitive.ScrollAreaScrollbar
      data-slot="scroll-area-scrollbar"
      orientation={orientation}
      className={cn(
        "pointer-events-none flex touch-none p-px opacity-0 transition-[opacity,color] duration-150 motion-reduce:transition-none select-none group-hover/scroll-area:pointer-events-auto group-hover/scroll-area:opacity-100 group-data-[scrolling=true]/scroll-area:pointer-events-auto group-data-[scrolling=true]/scroll-area:opacity-100",
        orientation === "vertical" &&
          "h-full w-(--scrollbar-size) border-l border-l-transparent",
        orientation === "horizontal" &&
          "h-(--scrollbar-size) flex-col border-t border-t-transparent",
        className
      )}
      {...props}
    >
      <ScrollAreaPrimitive.ScrollAreaThumb
        data-slot="scroll-area-thumb"
        className="relative flex-1 rounded-full bg-border"
      />
    </ScrollAreaPrimitive.ScrollAreaScrollbar>
  )
}

export { ScrollArea, ScrollBar }
