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

const pep440 = /^(?:([0-9]+)!)?([0-9]+(?:\.[0-9]+)*)(?:(a|b|rc)([0-9]+))?(?:\.post([0-9]+))?(?:\.dev([0-9]+))?(?:\+[a-z0-9]+(?:\.[a-z0-9]+)*)?$/;
const preOrder = { a: 0, b: 1, rc: 2 } as const;

function versionParts(value: string) {
  const match = pep440.exec(value);
  if (!match || match[0] !== value) return null;
  const release = match[2]!.split(".").map(BigInt);
  while (release.length > 1 && release[release.length - 1] === 0n) release.pop();
  const preKind = match[3] as keyof typeof preOrder | undefined;
  const postNum = match[5] !== undefined ? BigInt(match[5]) : null;
  const devNum = match[6] !== undefined ? BigInt(match[6]) : null;
  const preRank = preKind === undefined ? (postNum === null && devNum !== null ? -1 : 3) : preOrder[preKind];
  const preNum = match[4] !== undefined ? BigInt(match[4]) : 0n;
  return {
    epoch: BigInt(match[1] ?? "0"),
    release,
    tail: [
      BigInt(preRank), preNum,
      postNum === null ? 0n : 1n, postNum ?? 0n,
      devNum === null ? 1n : 0n, devNum ?? 0n,
    ] as const,
  };
}

function isNewer(candidate: string, current: string | null) {
  const next = versionParts(candidate);
  const running = current && versionParts(current);
  if (!next || !running) return false;
  if (next.epoch !== running.epoch) return next.epoch > running.epoch;
  const length = Math.max(next.release.length, running.release.length);
  for (let index = 0; index < length; index++) {
    const a = next.release[index] ?? 0n, b = running.release[index] ?? 0n;
    if (a !== b) return a > b;
  }
  for (let index = 0; index < next.tail.length; index++) {
    if (next.tail[index] !== running.tail[index]) return next.tail[index]! > running.tail[index]!;
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
