import { Info } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import { ReasonMessage } from "../i18n/ReasonMessage";
import { Message } from "../i18n/Message";
import { sessionContentKinds, type SessionPage } from "./types";

export function SessionCoverage({ harness, coverage }: { harness: string; coverage: SessionPage["coverage"] }) {
  const { t } = useTranslation("taskDetail");
  return <Popover>
    <PopoverTrigger className="inline-flex shrink-0 cursor-pointer items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-xs whitespace-nowrap text-ink2 hover:bg-selected-row hover:text-ink">
      <Info aria-hidden className="size-3.5 text-ink3"/>
      {t("sessionView.coverage.title")}
    </PopoverTrigger>
    <PopoverContent align="end" className="w-[min(32rem,calc(100vw-2rem))]">
      <table data-slot="session-coverage" aria-label={t("sessionView.coverage.title")} className="w-full text-left text-xs leading-relaxed text-ink2">
        <caption className="mb-2 text-left font-mono text-ink">{harness}</caption>
        <tbody>{sessionContentKinds.map(kind => {
          const declaration = coverage[kind];
          return <tr key={kind}>
            <th scope="row" className="w-24 py-0.5 pr-4 align-top font-normal text-ink">{t(`sessionView.coverage.kinds.${kind}`)}</th>
            <td className="py-0.5">{declaration.status === "unavailable" ? <Message ns="taskDetail" id="sessionView.coverage.unavailable" values={{ reason: <ReasonMessage reason={declaration.reason} /> }} /> : declaration.status === "evidenced" ? t("sessionView.coverage.evidenced", { version: declaration.version }) : t("sessionView.coverage.unverified")}</td>
          </tr>;
        })}</tbody>
      </table>
    </PopoverContent>
  </Popover>;
}
