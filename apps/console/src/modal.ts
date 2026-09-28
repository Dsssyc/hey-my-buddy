import { useEffect } from "react";
import type { RefObject } from "react";

/**
 * A modal dialog makes everything outside its own subtree inert while it is
 * open (docs/decisions/016-human-console-design.md): background controls leave
 * the tab order and the accessibility tree instead of merely sitting behind a
 * backdrop. Every sibling along the dialog's ancestor chain is marked, so the
 * rule holds for a portaled dialog and for one rendered inside the page alike,
 * while the dialog's own ancestors stay reachable.
 *
 * The attribute is written directly—so a browser without the `inert` property
 * still sees it—and the previous state is restored on close, never clearing an
 * element that was already inert for another reason.
 */
export function useBackgroundInert(modal: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const dialog = modal.current;
    if (!dialog) return;
    const marked: { element: HTMLElement; wasInert: boolean }[] = [];
    for (let node: HTMLElement | null = dialog; node && node !== document.body; node = node.parentElement) {
      const parent: HTMLElement | null = node.parentElement;
      if (!parent) break;
      for (const sibling of parent.children) {
        if (sibling === node || !(sibling instanceof HTMLElement)) continue;
        marked.push({ element: sibling, wasInert: sibling.hasAttribute("inert") });
        sibling.setAttribute("inert", "");
      }
    }
    return () => {
      for (const { element, wasInert } of marked) {
        if (!wasInert) element.removeAttribute("inert");
      }
    };
  }, [modal]);
}
