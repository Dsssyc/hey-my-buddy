import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { setLanguageChoice } from "../i18n";
import chinese from "../i18n/zh/router-errors";
import english from "../i18n/en/router-errors";
import { RouterFailure } from "./RouterFailure";
import { routingCodeText } from "./routing-code";

afterEach(() => { cleanup(); setLanguageChoice("browser"); });

describe("Router failure records", () => {
  for (const language of ["zh", "en"] as const) it(`explains native failures and keeps unknown codes in a keyboard-accessible hint in ${language}`, async () => {
    setLanguageChoice(language);
    for (const [code, text] of Object.entries(language === "zh" ? chinese : english)) {
      expect(routingCodeText(code), code).toBe(text);
    }
    const user = userEvent.setup();
    const view = render(<TooltipProvider delayDuration={0}>
      <RouterFailure failure={{ code: "native-prompt-refused", params: {} }} />
      <RouterFailure failure="future-native-code" />
      <RouterFailure failure={{ code: "future-reason-code", params: { detail: "raw diagnostic" } }} />
    </TooltipProvider>);
    const native = screen.getByText(language === "zh" ? "harness 拒绝了任务提示" : "Harness refused the task prompt");
    expect(native.getAttribute("aria-description")).toBe("native-prompt-refused");
    expect(view.container.textContent).not.toMatch(/native-prompt-refused|future-native-code|future-reason-code|raw diagnostic/);
    const unknown = screen.getAllByText(language === "zh" ? "本次执行失败，原因暂无法说明" : "This run failed for an unrecognized reason");
    expect(unknown.map(node => node.getAttribute("aria-description"))).toEqual(["future-native-code", "future-reason-code"]);
    await user.hover(unknown[0]);
    expect((await screen.findByRole("tooltip")).textContent).toBe("future-native-code");
    await user.unhover(unknown[0]);
    await user.tab();
    expect(native).toBe(document.activeElement);
    // The real-browser check also verifies the focus-visible tooltip behavior.
    expect(native.tabIndex).toBe(0);
  });

  it("retains program reason parameters and updates a recorded native failure when language changes", () => {
    setLanguageChoice("zh");
    render(<TooltipProvider>
      <RouterFailure failure={{ code: "router-not-responding", params: { seconds: 60 } }} />
      <RouterFailure failure="native-prompt-refused" />
    </TooltipProvider>);
    expect(screen.getByText("Router 的模型在 60 秒内没有开始作答。")).toBeTruthy();
    act(() => setLanguageChoice("en"));
    expect(screen.getByText("The Router's model did not start responding within 60 seconds.")).toBeTruthy();
    expect(screen.getByText("Harness refused the task prompt")).toBeTruthy();
  });
});
