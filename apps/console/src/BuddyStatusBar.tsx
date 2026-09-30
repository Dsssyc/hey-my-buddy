import type { ConsoleView, RoutingMode, Snapshot } from "./types";
import { snapshotHarnesses } from "./api";
import { harnessName } from "./buddy-display";
import { buddyHash } from "./buddy-navigation";
import type { BuddyLocation } from "./buddy-navigation";
import { quotaView } from "./host-workflow";
import { isDecisionCandidate, isFastRouterCandidate } from "./policy";
import { healthSummary } from "./RoutingStatusBar";
import { parseReviewVerification } from "./HarnessReview";

const REVIEW_STATUS: Record<string, string> = { failed: "验证失败", running: "验证中", queued: "等待验证", stopping: "正在停止", unconfirmed: "停止未确认" };

/** A single shared strip: each recorded alert links to the place that can handle it. */
export function BuddyStatusBar({ data, snapshot, onNavigate }: { data: ConsoleView; snapshot: Snapshot; onNavigate: (location: BuddyLocation) => void }) {
  const rows = snapshotHarnesses(snapshot);
  const health = healthSummary(snapshot.routingHealth);
  const quotaRows = rows.filter(row => quotaView(row.quota)?.alert);
  return <div className="buddy-status" role="region" aria-label="全局状态">
    {(["fast", "review"] as RoutingMode[]).map(mode => {
      const id = data.configuration[mode === "fast" ? "fastRouterProfileId" : "reviewRouterProfileId"];
      const profile = data.profiles.find(p => p.profileId === id);
      const row = rows.find(item => item.adapter === profile?.adapter);
      const usable = mode === "fast" ? isFastRouterCandidate(profile) : isDecisionCandidate(profile);
      const certificate = row ? parseReviewVerification(row.reviewVerification, row) : null;
      const verify = mode === "review" && profile?.adapter === "codex" && row?.status === "ready"
        && profile.available && profile.enabled && !usable;
      const text = !id ? "未指定" : usable ? "可用" : verify
        ? (REVIEW_STATUS[certificate?.status ?? ""] ?? "待验证")
        : "需处理";
      const location = row && (verify || row.status !== "ready") ? { section: "harness" as const, target: row.adapter }
        : profile && !usable ? { section: "models" as const, target: profile.profileId }
          : { section: "router" as const, target: mode };
      return <a key={mode} href={buddyHash(location)} className={!usable ? "status-attention" : undefined}
        onClick={event => { event.preventDefault(); onNavigate(location); }}
        aria-label={`${mode === "fast" ? "快速" : "审阅"} Router ${text}${!usable ? "，去处理" : ""}`}>
        {mode === "fast" ? "快速" : "审阅"} Router：{text}
      </a>;
    })}
    {health.warning && <a href={buddyHash({ section: "router", target: "health" })} className="status-attention"
      onClick={event => { event.preventDefault(); onNavigate({ section: "router", target: "health" }); }}>{health.text} · 去处理</a>}
    <a href={buddyHash({ section: "harness" })} onClick={event => { event.preventDefault(); onNavigate({ section: "harness" }); }}>Harness {rows.length ? `可用 ${rows.filter(row => row.status === "ready").length}/${rows.length}` : "未记录"}</a>
    {quotaRows.map(row => <a key={row.adapter} className="status-attention" title="最近额度观测"
      href={buddyHash({ section: "harness", target: row.adapter })}
      onClick={event => { event.preventDefault(); onNavigate({ section: "harness", target: row.adapter }); }}>额度提醒：{harnessName(row.adapter)}</a>)}
  </div>;
}
