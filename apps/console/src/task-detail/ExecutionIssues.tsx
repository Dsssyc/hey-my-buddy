import { useTranslation } from "react-i18next";
import { ApiError, errorText } from "../api";
import { Moment, Section } from "./parts";
import type { Attempt } from "./types";

/** Finalizing has not proved that the native execution stopped. */
export function confirmingStop(attempt: Attempt | null): boolean {
  return attempt?.executionState === "finalizing" && !attempt.shutdownConfirmed;
}

/** Recorded diagnostics belong to the selected execution, never an earlier turn. */
export function ExecutionIssues({ attempt }: { attempt: Attempt | null }) {
  const { t } = useTranslation("taskDetail");
  if (!attempt?.issues.length) return null;
  return <Section name={t("issues.name")}>
    <ul data-slot="execution-issues" className="space-y-3">
      {attempt.issues.map(issue => {
        const summary = errorText(new ApiError(issue.code, issue.message));
        const uncertain = issue.code === "EXECUTION_UNKNOWN" || issue.code === "SHUTDOWN_UNCONFIRMED";
        return <li key={issue.stage} className="space-y-1">
          <p className={uncertain ? "text-hand" : "text-fail"}>{summary}</p>
          {summary !== issue.message && <p className="text-ink2">{issue.message}</p>}
          <p className="flex flex-wrap gap-x-3 text-xs text-ink3">
            <span>{t(`issues.stages.${issue.stage}`, { defaultValue: issue.stage })}</span>
            <Moment at={issue.observedAt} />
          </p>
        </li>;
      })}
    </ul>
  </Section>;
}
