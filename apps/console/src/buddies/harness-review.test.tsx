import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { HarnessHealth } from "../api";
import { fixture, harness, openHarness } from "./harness.fixtures";
import { fieldValue, paneBlock } from "./harness.testing";

afterEach(() => { cleanup(); sessionStorage.clear(); window.location.hash = ""; document.documentElement.dataset.theme = ""; });

const note = "资格来自 harness 的只读档位声明，不代表原生调用已验证。";
const review = () => fieldValue(paneBlock("位置"), "审阅");
const word = () => within(review()).getByText(/^(可用|不可用|未知)$/);

describe("declared review level", () => {
  it.each(["codex", "claude", "dsh", "zcode"])("reads %s's read level without a model call", async name => {
    const f = fixture({ harnesses: [harness({ harness: name, status: "ready", permissions: { read: "enforced" } })] });
    await openHarness(f);
    expect(word().textContent).toBe("可用");
    expect(word().getAttribute("aria-description")).toBe(note);
    expect(screen.queryByRole("button", { name: /验证/ })).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });

  it.each([
    [{ read: "convention" }, "可用"],
    [{ write: "enforced" }, "不可用"],
    [undefined, "未知"],
  ] as [HarnessHealth["permissions"], string][])("derives review availability from declarations %o", async (permissions, text) => {
    await openHarness(fixture({ harnesses: [harness({ harness: "codex", status: "ready", permissions })] }));
    expect(word().textContent).toBe(text);
    expect(word().getAttribute("aria-description")).toBe(note);
  });
});

describe("declared web level", () => {
  it.each([["enforced", "强制限制"], ["convention", "未声明强制限制"]] as const)("shows %s with a visible description", async (web, expected) => {
    await openHarness(fixture({ harnesses: [harness({ harness: "codex", status: "ready", permissions: { web } })] }));
    expect(fieldValue(paneBlock("位置"), "只联网").textContent).toBe(expected);
    expect(paneBlock("位置").textContent).toContain("只联网档位只允许使用联网工具，可供画像面使用。");
  });
});
