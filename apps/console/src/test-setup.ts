// The suite reads the Chinese text (ADR-029); jsdom reports an English browser.
Object.defineProperty(window.navigator, "languages", { value: ["zh-CN"], configurable: true });
// Loaded after the line above on purpose: the text picks its language when it loads.
await import("./i18n");

export {};
