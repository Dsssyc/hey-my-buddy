import i18n from "i18next";
import { ApiError } from "./api";
/** A manual refresh waits for an overlapping poll before starting its own read. */
export async function waitForRead(isPending: () => boolean) {
  const deadline = Date.now() + 30_000;
  while (isPending()) {
    if (Date.now() >= deadline) throw new ApiError("READ_PENDING", () => i18n.t("console:aReadIsStillInProgressTry"));
    await new Promise(resolve => setTimeout(resolve, 50));
  }
}
