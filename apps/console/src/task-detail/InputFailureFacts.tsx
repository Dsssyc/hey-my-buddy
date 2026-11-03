import { useTranslation } from "react-i18next";
import { formatNumber } from "../i18n/format";
import { INPUT_PARTS } from "./input-failure";
import type { InputFailure } from "./input-failure";
import { Facts } from "./parts";

/** The declaration and the measured input stay distinct, including a native observed limit. */
export function InputFailureFacts({ failure }: { failure: InputFailure }) {
  const { t } = useTranslation("taskDetail");
  const { harness, limit, measurement } = failure;
  const bytes = measurement.utf8Parts;
  const fact = (value: boolean | null) => t(value === null ? "unknown" : value ? "inputFailure.yes" : "inputFailure.no");
  return <div data-slot="input-failure" className="space-y-3 text-sm">
    <p className="font-medium">{t(`inputFailure.stage.${failure.stage}`)}</p>
    <Facts items={[
      [t("inputFailure.harness"), <span key="harness" className="font-mono text-xs">{harness.name} · {harness.form} · {harness.version ?? t("unrecorded")}</span>],
      [t("inputFailure.limit"), limit.state === "known" ? `${formatNumber(limit.value)} ${t(`inputFailure.unit.${limit.unit}`)}` : t("inputFailure.unknownLimit")],
      ...(failure.max_chars !== undefined ? [[t("inputFailure.observedLimit"), `${formatNumber(failure.max_chars)} ${t("inputFailure.nativeChars")}`] as [string, string]] : []),
      ...(failure.actual_chars !== undefined ? [[t("inputFailure.observedTotal"), `${formatNumber(failure.actual_chars)} ${t("inputFailure.nativeChars")}`] as [string, string]] : []),
    ]} />
    <table className="w-full text-xs tabular-nums">
      <thead className="text-ink3"><tr>
        <th scope="col" className="pb-2 text-left font-normal">{t("inputFailure.measured")}</th>
        <th scope="col" className="pb-2 pl-3 text-right font-normal">{t(`inputFailure.unit.${measurement.unit}`)}</th>
        {bytes && <th scope="col" className="pb-2 pl-3 text-right font-normal">{t("inputFailure.unit.utf-8-bytes")}</th>}
      </tr></thead>
      <tbody>{INPUT_PARTS.map(part => <tr key={part}>
        <th scope="row" className="py-1 text-left font-normal text-ink2">{t(`inputFailure.part.${part}`)}</th>
        <td className="py-1 pl-3 text-right">{formatNumber(measurement.parts[part])}</td>
        {bytes && <td className="py-1 pl-3 text-right">{formatNumber(bytes[part])}</td>}
      </tr>)}</tbody>
      <tfoot className="border-t"><tr>
        <th scope="row" className="pt-2 text-left font-medium">{t("inputFailure.total")}</th>
        <td className="pt-2 pl-3 text-right font-medium">{formatNumber(measurement.total)}</td>
        {bytes && <td className="pt-2 pl-3 text-right font-medium">{formatNumber(measurement.utf8Bytes!)}</td>}
      </tr></tfoot>
    </table>
    <Facts items={[
      [t("inputFailure.sent"), fact(failure.nativeRequestSent)],
      [t("inputFailure.started"), fact(failure.modelStarted)],
    ]} />
  </div>;
}
