import { useTranslation } from "react-i18next";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import { reasonText } from "../i18n/reason";
import { sessionContentKinds, type SessionPage } from "./types";

export function SessionCoverage({ harness, coverage }: { harness: string; coverage: SessionPage["coverage"] }) {
  const { t } = useTranslation("taskDetail");
  return <Popover>
    <PopoverTrigger className="ml-auto cursor-pointer text-xs text-ink2 hover:text-ink">{t("sessionView.coverage.title")}</PopoverTrigger>
    <PopoverContent align="end" className="w-[min(32rem,calc(100vw-2rem))]">
      <table data-slot="session-coverage" aria-label={t("sessionView.coverage.title")} className="w-full text-left text-xs leading-relaxed text-ink2">
        <caption className="mb-2 text-left font-mono text-ink">{harness}</caption>
        <tbody>{sessionContentKinds.map(kind => {
          const declaration = coverage[kind];
          return <tr key={kind}>
            <th scope="row" className="w-24 py-0.5 pr-4 align-top font-normal text-ink">{t(`sessionView.coverage.kinds.${kind}`)}</th>
            <td className="py-0.5">{declaration.status === "unavailable" ? t("sessionView.coverage.unavailable", { reason: reasonText(declaration.reason) }) : declaration.status === "evidenced" ? t("sessionView.coverage.evidenced", { version: declaration.version }) : t("sessionView.coverage.unverified")}</td>
          </tr>;
        })}</tbody>
      </table>
    </PopoverContent>
  </Popover>;
}
