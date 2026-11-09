import i18n from "i18next";
import type copy from "../i18n/zh/router-errors";

/** Historical codes have their own short text; current settings never supply missing historical parameters. */
export function routingCodeText(code: string): string {
  if (!i18n.exists(`buddies:routerErrors.${code}`)) throw new Error(`Unrecognized routing error code: ${code}`);
  return i18n.t(`routerErrors.${code as keyof typeof copy}`, { ns: "buddies" });
}
