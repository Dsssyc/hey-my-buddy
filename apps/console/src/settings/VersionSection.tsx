import { useCopyState } from "../i18n/use-copy-state";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { errorText, type ConsoleApi } from "../api";
import type { RuntimeVersionInfo, VersionFacts } from "../types";
import { formatDateTime, formatNumber } from "../i18n/format";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "../components/ui/collapsible";
import { StateMark } from "../components/ui/state-mark";
import { CodeBlockCopyButton } from "../components/ui/code-block";
import { Section } from "./Section";

function Facts({ value }: { value: VersionFacts }) {
  const { t } = useTranslation("settings");
  const [open, setOpen] = useState(false);
  return <div className="space-y-1">
    <dl><dt className="sr-only">{t("version.software")}</dt><dd className="font-medium tabular-nums">{value.softwareVersion ?? t("version.unrecorded")}</dd></dl>
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-ink2">
        <dl className="flex flex-wrap gap-x-2"><dt>{t("version.installedAt")}</dt><dd className="tabular-nums">{value.installedAt ? formatDateTime(Date.parse(value.installedAt)) : t("version.unrecorded")}</dd></dl>
        <CollapsibleTrigger className="group -ml-2.5 inline-flex h-7 items-center gap-2 rounded-md px-2.5 text-xs"><Disclosure open={open} />{t("version.details")}</CollapsibleTrigger>
      </div>
      <CollapsibleContent>
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-5 gap-y-2 py-2 text-xs">
          <dt className="text-ink2">{t("version.contract")}</dt><dd className="tabular-nums">{value.contractVersion ?? t("version.unrecorded")}</dd>
          <dt className="text-ink2">{t("version.schema")}</dt><dd className="tabular-nums">{value.schemaVersion === null ? t("version.unrecorded") : formatNumber(value.schemaVersion)}</dd>
          <dt className="text-ink2">{t("version.sourceCommit")}</dt><dd className="min-w-0 break-all font-mono">{value.sourceCommit ?? t("version.unrecorded")}</dd>
        </dl>
      </CollapsibleContent>
    </Collapsible>
  </div>;
}

const numeric = /^(0|[1-9]\d*)$/;
const semver = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;

function versionParts(value: string) {
  const match = semver.exec(value);
  if (!match || match[0] !== value) return null;
  const prerelease = match[4]?.split(".");
  if (prerelease?.some(part => /^\d+$/.test(part) && !numeric.test(part))) return null;
  return { core: match.slice(1, 4).map(BigInt), prerelease };
}

function isNewer(candidate: string, current: string | null) {
  const next = versionParts(candidate);
  const running = current && versionParts(current);
  if (!next || !running) return false;
  for (const [index, part] of next.core.entries()) {
    if (part !== running.core[index]) return part > running.core[index]!;
  }
  const a = next.prerelease, b = running.prerelease;
  if (!a || !b) return !a && !!b;
  for (const [index, part] of a.entries()) {
    const other = b[index];
    if (other === undefined) return true;
    if (part === other) continue;
    if (numeric.test(part)) return numeric.test(other) && BigInt(part) > BigInt(other);
    return numeric.test(other) || part > other;
  }
  return false;
}

/** Read once on the first visible visit, including StrictMode and hide/show. */
export function VersionSection({ api, active }: { api: ConsoleApi; active: boolean }) {
  const { t } = useTranslation("settings");
  const [info, setInfo] = useState<RuntimeVersionInfo | null>(null), [error, setError] = useCopyState();
  const [published, setPublished] = useState<string | null>(null);
  const publication = useRef<ReturnType<ConsoleApi["publishedVersion"]> | null>(null);
  const pending = useRef<Promise<RuntimeVersionInfo> | null>(null);
  useEffect(() => {
    if (!active) return;
    let current = true;
    pending.current ??= api.runtimeVersion();
    publication.current ??= api.publishedVersion();
    void publication.current.then(value => { if (current) setPublished(value?.publishedVersion ?? null); });
    pending.current.then(value => { if (current) setInfo(value); }).catch(reason => { if (current) setError(() => errorText(reason)); });
    return () => { current = false; };
  }, [api, active]);
  const available = info && published && isNewer(published, info.running.softwareVersion) ? published : null;
  const upgrade = available ? `uvx hey-my-buddy@${available} install` : null;
  return <Section title={t("version.title")}>
    {error ? <p role="alert" className="flex items-center gap-2 text-xs text-fail"><StateMark kind="fail" />{t("version.failed", { error })}</p>
      : info ? <div className="space-y-5">
        <div className="space-y-3">{info.running.mode === "source" && <h3 className="text-xs text-ink2">{t("version.runningSource")}</h3>}<Facts value={info.running} /></div>
        {info.running.mode === "source" && <div className="space-y-3"><h3 className="text-xs text-ink2">{t("version.installedRuntime")}</h3>{info.installed ? <Facts value={info.installed} /> : <p className="text-xs text-ink2">{t("version.unrecorded")}</p>}</div>}
        {upgrade && <div data-slot="published-version" className="space-y-1 text-xs">
          <p className="text-ink2">{t("version.available", { version: available })}</p>
          <div className="flex min-w-0 items-center gap-2"><code className="min-w-0 wrap-anywhere">{upgrade}</code><CodeBlockCopyButton value={upgrade}/></div>
        </div>}
      </div> : <p className="text-xs text-ink2" role="status">{t(active ? "version.loading" : "version.unread")}</p>}
  </Section>;
}
