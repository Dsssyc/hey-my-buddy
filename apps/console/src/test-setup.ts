// The suite reads the Chinese text (ADR-029); jsdom reports an English browser.
Object.defineProperty(window.navigator, "languages", { value: ["zh-CN"], configurable: true });
// Loaded after the line above on purpose: the text picks its language when it loads.
await import("./i18n");

// jsdom has no ResizeObserver. The scroll areas, lists and tooltips ask for one to
// follow a size that jsdom never computes, so one that never reports is enough here.
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
// Nor does it capture pointers or scroll: the select list calls these on its trigger and its options.
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= () => {};
Element.prototype.scrollIntoView ??= () => {};
Element.prototype.scrollTo ??= () => {};

// Only semantic label-selection tests use this deterministic font metric. Real
// glyph widths and collision geometry are checked in Playwright.
Object.defineProperty(SVGElement.prototype, "getComputedTextLength", { configurable: true, value() { return [...(this.textContent ?? "")].reduce((width, char) => width + (/[^\x00-\x7f]/.test(char) ? 12 : 6), 0); } });

export {};
