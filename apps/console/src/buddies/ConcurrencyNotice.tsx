import { useTranslation } from "react-i18next";

/** Always visible beside the model's concurrency setting, including in read-only sessions. */
export function ConcurrencyNotice({ id }: { id: string }) {
  const { t } = useTranslation("common");

  return <div id={id} role="note" aria-labelledby={`${id}-title`}
    className="col-span-2 my-1 border-t border-line pt-3 text-sm leading-relaxed text-ink2">
    <h4 id={`${id}-title`} className="font-medium text-foreground">{t("concurrencyNotice.title")}</h4>
    <p className="mt-1.5">{t("concurrencyNotice.body")}</p>
  </div>;
}
