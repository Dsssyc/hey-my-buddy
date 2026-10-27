import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../App";
import i18n from "i18next";
import { fixture, snapshot } from "./roles.testing";

const MODEL = "claude / anthropic / claude-sonnet-5";
const languages = [
  {
    language: "zh", limit: "并发上限", note: "备注",
    title: "同时运行多个会话之前",
    body: "同时运行的会话越多，同一时间消耗的额度与请求就越多。一些订阅是按个人的日常使用设计的，超出它的条款或建议的并发，可能被限流、暂停，甚至影响账户。调高之前，请先查看你所用 harness 与订阅的条款。",
  },
  {
    language: "en", limit: "Concurrency limit", note: "Note",
    title: "Before running several sessions at once",
    body: "Running more sessions at once uses more of your quota and requests at the same time. Some subscriptions are meant for one person's ordinary use; going beyond their terms or suggested concurrency can lead to rate limits, suspension or effects on your account. Check the terms of the harnesses and subscriptions you use before raising the limit.",
  },
];

async function openDrawer(sessionPresent = true) {
  const user = userEvent.setup();
  window.location.hash = "#buddies/catalog";
  render(<App suppliedApi={fixture(snapshot({ sessionPresent })).api} />);
  await user.click(await screen.findByRole("button", { name: MODEL }));
  return screen.getByRole("region", { name: MODEL });
}

afterEach(async () => {
  cleanup();
  window.location.hash = "";
  window.localStorage.clear();
  await i18n.changeLanguage("zh");
});

describe("the concurrency notice in the model settings", () => {
  for (const copy of languages) for (const sessionPresent of [true, false]) {
    it(`shows the full ${copy.language} copy and associates it with the limit with ${sessionPresent ? "a session" : "no session"}`, async () => {
      await i18n.changeLanguage(copy.language);
      const drawer = await openDrawer(sessionPresent);
      const notice = within(drawer).getByRole("note", { name: copy.title });
      expect(within(notice).getByRole("heading", { name: copy.title }).textContent).toBe(copy.title);
      expect(within(notice).getByText(copy.body).textContent).toBe(copy.body);
      const input = within(drawer).getByRole<HTMLInputElement>("spinbutton", { name: copy.limit });
      const descriptions = input.getAttribute("aria-describedby")!.split(" ").map(id => document.getElementById(id)!);
      expect(descriptions).toContain(notice);
      expect(descriptions.map(element => element.textContent).join(" ")).toContain(copy.body);
      expect(input.readOnly).toBe(!sessionPresent);
      expect(within(drawer).getByRole("textbox", { name: copy.note })).toBeTruthy();
      expect(notice.closest("[role=tooltip]")).toBeNull();
    });
  }

  it("updates the visible notice and the input's description when the mounted page changes language", async () => {
    const drawer = await openDrawer();
    const input = within(drawer).getByRole("spinbutton", { name: languages[0].limit });
    const descriptionIds = input.getAttribute("aria-describedby");
    await act(async () => { await i18n.changeLanguage("en"); });
    const notice = within(drawer).getByRole("note", { name: languages[1].title });
    expect(within(notice).getByText(languages[1].body)).toBeTruthy();
    expect(within(drawer).queryByText(languages[0].body)).toBeNull();
    expect(input.getAttribute("aria-describedby")).toBe(descriptionIds);
    expect(descriptionIds!.split(" ")).toContain(notice.id);
  });
});
