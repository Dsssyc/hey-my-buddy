import type { ComponentProps } from "react";
import { expectTypeOf, it } from "vitest";
import { CopyableIdentifier } from "../components/CopyableIdentifier";
import { Section } from "../components/section";
import { Header } from "./Header";
import type { Read } from "./client";
import type { Activity, Attempt } from "./types";

it("keeps retired evidence fields and hover-only props out of the detail types", () => {
  expectTypeOf<Extract<keyof Attempt, "session">>().toEqualTypeOf<never>();
  expectTypeOf<Extract<keyof Activity, "lastToolAt" | "lastNativeAt" | "eventSeq">>().toEqualTypeOf<never>();
  expectTypeOf<Extract<keyof Read<unknown>, "verifiedAtMs">>().toEqualTypeOf<never>();
  expectTypeOf<Extract<keyof ComponentProps<typeof Header>, "nowMs" | "verifiedAtMs">>().toEqualTypeOf<never>();
  expectTypeOf<Extract<keyof ComponentProps<typeof CopyableIdentifier>, "hint">>().toEqualTypeOf<never>();
  expectTypeOf<Extract<keyof ComponentProps<typeof Section>, "hint">>().toEqualTypeOf<never>();
});
