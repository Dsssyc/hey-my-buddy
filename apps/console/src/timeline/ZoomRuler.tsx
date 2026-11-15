import { forwardRef, useCallback, useEffect, useRef, useState } from "react";
import { Tooltip as TooltipPrimitive } from "radix-ui";
import { TOOLTIP_DELAY_MS, Tooltip, TooltipContent } from "../components/ui/tooltip";
import type { ComponentPropsWithoutRef, CSSProperties, ReactNode } from "react";
import { ScanLine } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Hint } from "../components/hint";
import { Button } from "../components/ui/button";

const HIDE_DRIFT_PX = 3;

/** Native tooltip events stay on the ruler; its reference measures the opening point. */
const RulerTrigger = forwardRef<HTMLSpanElement, {
  rulerRef: (node: HTMLDivElement | null) => void;
  anchorOffset: number;
  rulerProps: ComponentPropsWithoutRef<"div">;
  children: ReactNode;
  "aria-describedby"?: string;
  "aria-description"?: string;
  "data-state"?: string;
}>(function RulerTrigger({ rulerRef, anchorOffset, rulerProps, children,
  "aria-describedby": ariaDescribedBy, "aria-description": ariaDescription, "data-state": dataState }, ref) {
  return <div {...rulerProps} ref={rulerRef} aria-describedby={ariaDescribedBy} aria-description={ariaDescription} data-state={dataState}>
    <span ref={ref} aria-hidden="true" className="pointer-events-none absolute top-0 bottom-0 w-0" style={{ left: anchorOffset }}/>
    {children}
  </div>;
});

/** The axis surface is also its discoverable zoom control. */
export const ZoomRuler = forwardRef<HTMLDivElement, {
  children: ReactNode;
  atMinimum: boolean;
  onChange: (direction: -1 | 0 | 1) => void;
  viewportWidth: number;
  className?: string;
  style?: CSSProperties;
}>(function ZoomRuler({ children, atMinimum, onChange, viewportWidth, className, style }, ref) {
  const { t } = useTranslation("tasks");
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const rulerNode = useRef<HTMLDivElement | null>(null);
  const [anchorOffset, setAnchorOffset] = useState(0);
  const [hintOpen, setHintOpen] = useState(false);
  const dwellTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastPointer = useRef<{ x: number; y: number } | null>(null);
  const openOrigin = useRef<{ x: number; y: number } | null>(null);
  const clearDwell = useCallback(() => {
    if (dwellTimer.current !== null) {
      clearTimeout(dwellTimer.current);
      dwellTimer.current = null;
    }
  }, []);
  useEffect(() => clearDwell, [clearDwell]);
  const dismiss = useCallback(() => {
    clearDwell();
    openOrigin.current = null;
    setHintOpen(false);
  }, [clearDwell]);
  const attach = useCallback((node: HTMLDivElement | null) => {
    rulerNode.current = node;
    setElement(node);
    if (typeof ref === "function") ref(node); else if (ref) ref.current = node;
  }, [ref]);
  const viewport = element?.closest<HTMLElement>("[data-radix-scroll-area-viewport]");
  const bounds = viewport?.getBoundingClientRect();
  return <Tooltip open={hintOpen} onOpenChange={open => {
    if (!open) dismiss();
  }}><TooltipPrimitive.Trigger asChild aria-description={t("zoomRulerHint")}><RulerTrigger rulerRef={attach} anchorOffset={anchorOffset} rulerProps={{
    "data-slot": "zoom-ruler",
    tabIndex: 0,
    className: cn("relative bg-ruler outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring", className),
    style: { touchAction: "pan-x", ...style },
    onPointerMove: event => {
      if (event.pointerType === "touch") return;
      if ((event.target as Element).closest("[data-zoom-exclude]")) {
        lastPointer.current = null;
        dismiss();
        return;
      }
      const x = event.clientX;
      const y = event.clientY;
      const prev = lastPointer.current;
      if (prev && prev.x === x && prev.y === y) return;
      lastPointer.current = { x, y };
      if (openOrigin.current) {
        if (Math.hypot(x - openOrigin.current.x, y - openOrigin.current.y) < HIDE_DRIFT_PX) return;
        openOrigin.current = null;
        setHintOpen(false);
      } else if (hintOpen) {
        setHintOpen(false);
      }
      clearDwell();
      dwellTimer.current = setTimeout(() => {
        dwellTimer.current = null;
        const node = rulerNode.current;
        if (!node) return;
        openOrigin.current = { x, y };
        setAnchorOffset(x - node.getBoundingClientRect().left);
        setHintOpen(true);
      }, TOOLTIP_DELAY_MS);
    },
    onPointerLeave: () => { lastPointer.current = null; dismiss(); },
    onPointerCancel: () => { lastPointer.current = null; dismiss(); },
    onPointerDown: () => { dismiss(); },
    onWheel: () => { dismiss(); },
    onFocus: event => {
      if (event.target !== event.currentTarget || !(event.target as Element).matches(":focus-visible")) return;
      clearDwell();
      openOrigin.current = null;
      const node = rulerNode.current;
      const view = node?.closest<HTMLElement>("[data-radix-scroll-area-viewport]");
      if (node && view) {
        setAnchorOffset(view.getBoundingClientRect().left + view.clientWidth / 2 - node.getBoundingClientRect().left);
      }
      setHintOpen(true);
    },
    onBlur: event => {
      if (event.target !== event.currentTarget) return;
      dismiss();
    },
    onKeyDown: event => {
      if (event.target !== event.currentTarget) return;
      if (event.key === "+" || event.key === "=") { event.preventDefault(); onChange(1); }
      else if (event.key === "-") { event.preventDefault(); onChange(-1); }
      else if (event.key === "0") { event.preventDefault(); onChange(0); }
    },
  } as ComponentPropsWithoutRef<"div"> & { "data-slot": string }}>
    {children}
    {!atMinimum && <div className="pointer-events-none absolute inset-0 z-50">
      <div className="sticky left-0 flex h-full items-start justify-end pr-2" style={{ width: viewportWidth }}>
        <Hint text={t("zoomFit")}><Button type="button" variant="ghost" size="icon-sm" data-slot="zoom-fit" aria-label={t("zoomFit")}
          className="pointer-events-auto"
          onClick={() => onChange(0)}><ScanLine aria-hidden="true"/></Button></Hint>
      </div>
    </div>}
  </RulerTrigger></TooltipPrimitive.Trigger>
    <TooltipContent side="bottom"
      collisionPadding={{ left: (bounds?.left ?? 0) + 8, right: window.innerWidth - (bounds?.right ?? window.innerWidth) + 8, top: 8, bottom: 8 }}
      style={{ width: "max-content", maxWidth: "var(--radix-tooltip-content-available-width)" }}>{t("zoomRulerHint")}</TooltipContent>
  </Tooltip>;
});
