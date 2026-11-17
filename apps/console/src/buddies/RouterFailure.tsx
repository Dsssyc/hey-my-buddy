import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { ReasonMessage } from "../i18n/ReasonMessage";
import reasons from "../i18n/zh/reasons";
import type { Reason } from "../reason";
import { routingCodeText } from "./routing-code";

/** Recorded native codes are open-ended; program reasons retain their named facts. */
export function RouterFailure({ failure }: { failure: string | Reason }) {
  useTranslation("buddies");
  const reason = typeof failure === "string" ? null : failure;
  const code = typeof failure === "string" ? failure : failure.code;
  const registered = reason !== null && Object.hasOwn(reasons, code);
  const detail = registered
    ? reason.code === "maintenance-answer-invalid" && Array.isArray(reason.params.errors)
      ? reason.params.errors.map(String).join("; ") : undefined
    : code;
  return <Hint text={detail}><span data-slot="router-failure" tabIndex={detail ? 0 : undefined}>
    {registered ? <ReasonMessage reason={reason} /> : routingCodeText(code)}
  </span></Hint>;
}
