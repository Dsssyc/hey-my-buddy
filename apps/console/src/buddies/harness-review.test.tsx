import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { HarnessHealth } from "../api";
import { fixture, harness, openHarness } from "./harness.fixtures";
import { fieldValue, paneBlock } from "./harness.testing";

afterEach(() => { cleanup(); sessionStorage.clear(); window.location.hash = ""; document.documentElement.dataset.theme = ""; });

const note = "按 harness 的只读档位声明显示；此处不运行原生调用。";
const review = () => fieldValue(paneBlock("位置"), "只读");
const word = () => within(review()).getByText(/^(强制限制|依照约定|未声明|未知)$/);

describe("declared review level", () => {
  it("shows installed and verification versions independently, including an unchecked declaration", async () => {
    await openHarness(fixture({ harnesses: [harness({ harness: "dsh", status: "ready", version: "0.4.2", permissionsVerifiedVersion: null })] }));
    const location = paneBlock("位置");
    expect(fieldValue(location, "已安装版本").textContent).toBe("0.4.2");
    expect(fieldValue(location, "核实时的版本").textContent).toBe("未核对");
    expect(screen.getByRole("group", { name: "DSH 设置" }).querySelectorAll(":scope > [role=group]")).toHaveLength(2);
    expect(within(paneBlock("账户")).queryByRole("button")).toBeNull();
  });

  it.each(["codex", "claude", "dsh", "zcode"])("reads %s's read level without a model call", async name => {
    const f = fixture({ harnesses: [harness({ harness: name, status: "ready", permissions: { read: "enforced" } })] });
    await openHarness(f);
    expect(word().textContent).toBe("强制限制");
    expect(word().getAttribute("aria-description")).toBe(note);
    expect(screen.queryByRole("button", { name: /验证/ })).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });

  it.each([
    [{ read: "convention" }, "依照约定"],
    [{ write: "enforced" }, "未声明"],
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
    expect(paneBlock("位置").textContent).toContain(web === "enforced"
      ? "只联网档位已声明强制执行，可构建模型画像。"
      : "只联网档位未声明强制执行，不能构建模型画像。");
  });
});
