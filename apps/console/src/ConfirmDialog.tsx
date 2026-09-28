import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";
import { useBackgroundInert } from "./modal";

/**
 * Modal confirmation with a real focus trap: focus starts on the safe cancel
 * action so Enter cannot confirm by accident, Tab wraps inside the dialog and
 * Escape cancels. The background content is inert while the dialog is open,
 * and the caller restores focus to the control that opened it.
 */
export function ConfirmDialog({ title, children, confirmLabel, onConfirm, onCancel }: {
  title: string; children: ReactNode; confirmLabel: string;
  onConfirm: () => void; onCancel: () => void;
}) {
  const id = useId();
  const backdrop = useRef<HTMLDivElement>(null);
  const initialFocus = useRef<HTMLButtonElement>(null);
  useBackgroundInert(backdrop);
  useEffect(() => { initialFocus.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== "Tab") return;
      const items = [...(backdrop.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? [])];
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      const active = document.activeElement;
      if (!backdrop.current?.contains(active)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return createPortal(<div className="dialog-backdrop" ref={backdrop}>
    <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-body`}>
      <h2 id={`${id}-title`}>{title}</h2>
      <p id={`${id}-body`}>{children}</p>
      <div className="actions">
        <button ref={initialFocus} type="button" className="button" onClick={onCancel}>取消</button>
        <button type="button" className="button primary" onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </div>
  </div>, document.body);
}
