import { forwardRef, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Tooltip as TooltipPrimitive } from "radix-ui";
import { Tooltip, TooltipContent } from "../components/ui/tooltip";
import type { CSSProperties, ReactNode } from "react";
import { ScanLine } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Hint } from "../components/hint";
import { Button } from "../components/ui/button";

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
  const [content, setContent] = useState<HTMLDivElement | null>(null);
  const [contentWidth, setContentWidth] = useState(0);
  const [anchorOffset, setAnchorOffset] = useState(0);
  const pointer = useRef<number | null>(null);
  const attach = useCallback((node: HTMLDivElement | null) => {
    setElement(node);
    if (typeof ref === "function") ref(node); else if (ref) ref.current = node;
  }, [ref]);
  const viewport = element?.closest<HTMLElement>("[data-radix-scroll-area-viewport]");
  const bounds = viewport?.getBoundingClientRect();
  useLayoutEffect(() => {
    if (!content) return;
    const measure = () => setContentWidth(content.getBoundingClientRect().width);
    const observer = new ResizeObserver(measure);
    observer.observe(content); measure();
    return () => observer.disconnect();
  }, [content]);
  useEffect(() => {
    if (!element || !viewport) return;
    const follow = () => setAnchorOffset((pointer.current ?? viewport.getBoundingClientRect().left + viewport.clientWidth / 2) - element.getBoundingClientRect().left);
    viewport.addEventListener("scroll", follow, { passive: true });
    return () => viewport.removeEventListener("scroll", follow);
  }, [element, viewport]);
  return <Tooltip><TooltipPrimitive.Trigger asChild aria-description={t("zoomRulerHint")}
    onFocus={event => {
      if (!(event.target as Element).matches(":focus-visible")) { event.preventDefault(); return; }
      pointer.current = null;
      setAnchorOffset((bounds!.left + viewport!.clientWidth / 2) - element!.getBoundingClientRect().left);
    }}><div ref={attach} data-slot="zoom-ruler" tabIndex={0}
    className={cn("relative bg-ruler outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring", className)} style={{ touchAction: "pan-x", ...style }}
    onPointerMove={event => {
      pointer.current = event.clientX;
      setAnchorOffset(event.clientX - event.currentTarget.getBoundingClientRect().left);
    }}
    onKeyDown={event => {
      if (event.target !== event.currentTarget) return;
      if (event.key === "+" || event.key === "=") { event.preventDefault(); onChange(1); }
      else if (event.key === "-") { event.preventDefault(); onChange(-1); }
      else if (event.key === "0") { event.preventDefault(); onChange(0); }
    }}>
    {children}
    {!atMinimum && <div className="pointer-events-none absolute inset-0 z-50">
      <div className="sticky left-0 flex h-full items-start justify-end pr-2" style={{ width: viewportWidth }}>
        <Hint text={t("zoomFit")}><Button type="button" variant="ghost" size="icon-sm" data-slot="zoom-fit" aria-label={t("zoomFit")}
          className="pointer-events-auto"
          onClick={() => onChange(0)}><ScanLine aria-hidden="true"/></Button></Hint>
      </div>
    </div>}
  </div></TooltipPrimitive.Trigger>
    <TooltipContent ref={setContent} side="bottom" sideOffset={8} align="start" alignOffset={anchorOffset - contentWidth / 2} sticky="always"
      collisionPadding={{ left: (bounds?.left ?? 0) + 8, right: window.innerWidth - (bounds?.right ?? window.innerWidth) + 8, top: 8, bottom: 8 }}
      style={{ maxWidth: "min(20rem, var(--radix-tooltip-content-available-width))" }}>{t("zoomRulerHint")}</TooltipContent>
  </Tooltip>;
});
