import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { Message } from "./Message";

afterEach(cleanup);
it("lets Trans place rich values without interpreting user text as markup", () => {
  const { container } = render(<Message ns="reasons" id="alias.conflict" literalKey
    values={{ conflictingBuddy: <span data-slot="value">{"<img src=x> {{value}}"}</span> }} />);
  expect(container.textContent).toBe("这个别名已被 <img src=x> {{value}} 使用。");
  expect(container.querySelector('[data-slot=value]')).not.toBeNull();
  expect(container.querySelector("img")).toBeNull();
});
