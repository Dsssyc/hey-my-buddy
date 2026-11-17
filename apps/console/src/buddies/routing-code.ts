import i18n from "i18next";
import copy from "../i18n/zh/router-errors";

/** Historical codes have their own short text; current settings never supply missing historical parameters. */
export function routingCodeText(code: string): string {
  const key = Object.hasOwn(copy, code) ? code as keyof typeof copy : "unknown";
  return i18n.t(`routerErrors.${key}`, { ns: "buddies" });
}
