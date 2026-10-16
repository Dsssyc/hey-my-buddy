import i18n from "i18next";
import type { RoutingMode } from "./types";

/** Use only the recorded mode; missing historic facts stay unrecorded. */
export function recordedRoutingMode(mode: RoutingMode | null | undefined): string {
  return mode === "fast" ? i18n.t("console:fast") : mode === "review" ? i18n.t("console:review") : i18n.t("console:notRecorded");
}
