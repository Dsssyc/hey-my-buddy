import * as React from "react"
import { cn } from "cn"
import { RadioGroup as RadioGroupPrimitive } from "radix-ui"

type ThumbRect = { left: number; top: number; width: number; height: number }

// One choice out of a few, side by side: a radio group drawn as joined segments.
// The checked look follows aria-checked: a tooltip around an item overwrites its data-state.
function Segmented({
  className,
  children,
  ref,
  ...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Root>) {
  const rootRef = React.useRef<HTMLDivElement | null>(null)
  const thumbRef = React.useRef<HTMLSpanElement | null>(null)
  const mergedRef = React.useCallback((node: HTMLDivElement | null) => {
    rootRef.current = node
    if (typeof ref === "function") ref(node)
    else if (ref) ref.current = node
  }, [ref])

  React.useLayoutEffect(() => {
    const root = rootRef.current!
    const thumb = thumbRef.current!
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")
    let previous: { element: HTMLElement; rect: ThumbRect } | null = null
    let cleanupSlide: (() => void) | undefined

    const finish = () => {
      cleanupSlide?.()
      cleanupSlide = undefined
      root.removeAttribute("data-sliding")
      thumb.style.opacity = "0"
    }
    const place = ({ left, top, width, height }: ThumbRect) => {
      thumb.style.width = `${width}px`
      thumb.style.height = `${height}px`
      thumb.style.transform = `translate(${left}px, ${top}px)`
    }
    const measure = (animate: boolean) => {
      const active = root.querySelector<HTMLElement>('[role="radio"][aria-checked="true"]')
      if (!active || active.offsetWidth <= 0) {
        finish()
        previous = null
        root.removeAttribute("data-thumb-ready")
        return
      }
      const next = { left: active.offsetLeft, top: active.offsetTop, width: active.offsetWidth, height: active.offsetHeight }
      root.setAttribute("data-thumb-ready", "true")
      if (animate && previous && previous.element !== active && previous.rect.width > 0 && !reducedMotion?.matches) {
        let start = previous.rect
        if (root.getAttribute("data-sliding") === "true") {
          const current = thumb.getBoundingClientRect(), bounds = root.getBoundingClientRect()
          start = { left: current.left - bounds.left - root.clientLeft, top: current.top - bounds.top - root.clientTop, width: current.width, height: current.height }
        }
        finish()
        root.setAttribute("data-sliding", "true")
        thumb.style.transition = "none"
        thumb.style.opacity = "1"
        place(start)
        void thumb.offsetWidth
        thumb.style.removeProperty("transition")
        place(next)
        const onEnd = (event: TransitionEvent) => { if (event.target === thumb) finish() }
        thumb.addEventListener("transitionend", onEnd)
        const timeout = setTimeout(finish, 180)
        cleanupSlide = () => {
          thumb.removeEventListener("transitionend", onEnd)
          clearTimeout(timeout)
        }
      } else if (!animate || previous?.element !== active) {
        finish()
        thumb.style.transition = "none"
        place(next)
        void thumb.offsetWidth
        thumb.style.removeProperty("transition")
      }
      previous = { element: active, rect: next }
    }
    measure(false)
    const observer = new MutationObserver(() => measure(true))
    observer.observe(root, { subtree: true, attributes: true, attributeFilter: ["aria-checked"] })
    const resizeObserver = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => measure(false)) : null
    resizeObserver?.observe(root)
    root.querySelectorAll<HTMLElement>('[role="radio"]').forEach(item => resizeObserver?.observe(item))
    return () => {
      finish()
      observer.disconnect()
      resizeObserver?.disconnect()
    }
  }, [])

  return (
    <RadioGroupPrimitive.Root
      ref={mergedRef}
      data-slot="segmented"
      className={cn("group/segmented relative isolate inline-flex h-8 w-fit overflow-hidden rounded-md border border-line2", className)}
      {...props}
    >
      <span ref={thumbRef} aria-hidden="true" data-slot="segmented-thumb"
        className="pointer-events-none absolute top-0 left-0 bg-foreground opacity-0 transition-[transform,width,height] duration-150 ease-out motion-reduce:transition-none" />
      {children}
    </RadioGroupPrimitive.Root>
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
        "relative z-10 inline-flex h-full items-center justify-center border-l border-line2 px-3 text-sm whitespace-nowrap text-ink2 outline-none transition-[color,border-color,box-shadow] duration-150 first-of-type:border-l-0 hover:text-foreground motion-reduce:transition-none focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:opacity-50 aria-checked:bg-foreground group-data-[sliding=true]/segmented:aria-checked:bg-transparent aria-checked:text-card",
        className
      )}
      {...props}
    />
  )
}

export { Segmented, SegmentedItem }
