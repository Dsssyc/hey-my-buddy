import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { Facts, Paths, Section } from "./parts";
import type { Artifact, Integration } from "./types";

function EvidenceSection({ name, summary, children }: { name: string; summary: React.ReactNode; children: React.ReactNode }) {
  const [open, setOpen] = useState(true);
  return <Section name={name}><Collapsible open={open} onOpenChange={setOpen}>
    <CollapsibleTrigger className="flex max-w-full items-center gap-2 text-left text-sm">
      <Disclosure open={open} className="text-ink2"/><span className="min-w-0 break-all">{summary}</span>
    </CollapsibleTrigger>
    <CollapsibleContent><div className="pt-3">{children}</div></CollapsibleContent>
  </Collapsible></Section>;
}

export function DeliveryEvidence({ artifact }: { artifact: Artifact }) {
  const { t } = useTranslation("taskDetail");
  if (artifact.kind === "result") return <EvidenceSection name={t("delivery.fixedResult")} summary={<code className="text-xs">{artifact.artifactId}</code>}>
    <Facts items={[
      [t("seal.id"), artifact.artifactId, true],
      [t("delivery.location"), artifact.resultFile!.path, true],
      [t("seal.sha"), artifact.resultFile!.sha256, true],
    ]}/>
  </EvidenceSection>;

  const evidence = artifact.sealedEvidence;
  const source = artifact.integrationSource;
  return <>
    <EvidenceSection name={t("delivery.sealedEvidence")} summary={<code className="text-xs">{artifact.artifactId}</code>}>
      <Facts items={[
        [t("seal.id"), artifact.artifactId, true],
        [t("delivery.snapshotSha"), evidence?.snapshotSha256 ?? artifact.legacyEvidence?.manifestSha256 ?? t("unrecorded"), true],
        ...(artifact.legacyEvidence && artifact.diffPath ? [[t("seal.diff"), artifact.diffPath, true] as [string, string, boolean]] : []),
        ...(evidence?.tree ? [[t("delivery.tree"), evidence.tree, true] as [string, string, boolean]] : []),
        ...(artifact.changedPaths.length ? [[t("seal.paths"), <Paths key="paths" items={artifact.changedPaths}/>] as [string, React.ReactNode]] : []),
        ...(artifact.partial ? [[t("seal.note"), t("seal.partialNote")] as [string, string]] : []),
      ]}/>
    </EvidenceSection>
    {source && <EvidenceSection name={t("delivery.integrationSource")} summary={t(source.kind === "commits" ? "delivery.workerCommits" : "delivery.fixedPatch")}>
      <Facts items={source.kind === "commits" ? [
        [t("delivery.baseCommit"), source.baseCommit, true], [t("delivery.headCommit"), source.headCommit, true],
      ] : [
        ...(artifact.legacyEvidence ? [[t("seal.base"), source.baseCommit, true] as [string, string, boolean]] : []),
        [t("delivery.location"), source.path, true], [t("seal.sha"), source.sha256, true],
        ...(artifact.legacyEvidence && artifact.cumulativePatch ? [[t("seal.paths"), <Paths key="cumulative-paths" items={artifact.cumulativePatch.changedPaths}/>] as [string, React.ReactNode]] : []),
      ]}/>
    </EvidenceSection>}
  </>;
}

export function IntegrationEvidence({ integration }: { integration: Integration | null }) {
  const { t } = useTranslation("taskDetail");
  if (!integration) return <Section name={t("acceptance.integration")}><p className="text-ink2">{t("acceptance.noIntegration")}</p></Section>;
  const verification = integration.verification;
  return <>
    <EvidenceSection name={t("acceptance.integration")} summary={t(integration.state === "not-required" ? "acceptance.notRequired" : "acceptance.verified")}>
      <Facts items={[
        ...(integration.state !== "not-required" ? [
          ...(verification.version !== 2 ? [[t("acceptance.strategy"), t(`delivery.strategy.${integration.strategy}`)] as [string, string]] : []),
          ...(integration.target ? [[t("acceptance.target"), `${integration.target.path} @ ${integration.target.ref}`, true] as [string, string, boolean]] : []),
          ...(integration.beforeCommit ? [[t("delivery.beforeCommit"), integration.beforeCommit, true] as [string, string, boolean]] : []),
          ...(integration.afterCommit ? [[t("delivery.afterCommit"), integration.afterCommit, true] as [string, string, boolean]] : []),
        ] : []),
      ]}/>
    </EvidenceSection>
    {integration.state !== "not-required" && <Section name={t("delivery.verificationResult")}>
      <div className="space-y-3">
        <div className="text-sm">{t(verification.version === 2 ? integration.strategy === "three-way-adjusted" ? "delivery.adjusted" : "delivery.cleanMerge" : "delivery.historical")}</div>
        <Facts items={[
          ...(verification.note ? [[t("acceptance.note"), verification.note] as [string, string]] : []),
          ...(verification.conflictingPaths.length ? [[t("delivery.conflictingPaths"), <Paths key="conflicts" items={verification.conflictingPaths}/>] as [string, React.ReactNode]] : []),
          ...(verification.hostPaths.length ? [[t("delivery.hostPaths"), <Paths key="host" items={verification.hostPaths}/>] as [string, React.ReactNode]] : []),
          ...(verification.version !== 2 && verification.artifactMode ? [[t("delivery.artifactMode"), verification.artifactMode] as [string, string]] : []),
          ...(verification.version !== 2 && verification.artifactOid ? [[t("delivery.artifactOid"), verification.artifactOid, true] as [string, string, boolean]] : []),
          ...(verification.version !== 2 && verification.differingPaths.length ? [[t("delivery.differingPaths"),
            <ul key="diff" className="space-y-1">{verification.differingPaths.map(item => <li key={item.path}>
              <code>{item.path}</code>{item.expectedMode && <span> · {t("delivery.expectedMode")} {item.expectedMode}</span>}
              {item.expectedOid && <span> · {t("delivery.expectedOid")} <code>{item.expectedOid}</code></span>}
              {item.artifactMode && <span> · {t("delivery.artifactMode")} {item.artifactMode}</span>}
              {item.artifactOid && <span> · {t("delivery.artifactOid")} <code>{item.artifactOid}</code></span>}
            </li>)}</ul>] as [string, React.ReactNode]] : []),
        ]}/>
        {verification.version !== 2 && verification.adjustments.length > 0 && <div><p className="text-xs text-ink3">{t("delivery.adjustments")}</p><Paths items={verification.adjustments}/></div>}
      </div>
    </Section>}
    {integration.note && verification.version !== 2 && <Section name={t("acceptance.note")}><p className="whitespace-pre-wrap">{integration.note}</p></Section>}
  </>;
}
