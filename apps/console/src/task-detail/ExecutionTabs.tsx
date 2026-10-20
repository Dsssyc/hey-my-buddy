import { lazy, Suspense, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Tabs as TabsPrimitive } from "radix-ui";
import { useTranslation } from "react-i18next";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import type { SessionClient } from "../session-view/use-session";
import type { SessionPosition } from "../session-view/types";
import type { DetailTab } from "./location";

const SessionPanel = lazy(() => import("../session-view/SessionPanel"));

/** Retain both reading surfaces while only the visible session owns reads. */
export function ExecutionTabs({ children, client, taskId, attemptId, harness, active, tab, onTab }: {
  children: ReactNode; client: SessionClient; taskId: string; attemptId: string; harness: string;
  active: boolean; tab: DetailTab; onTab: (tab: DetailTab) => void;
}) {
  const { t } = useTranslation("taskDetail");
  const [visited, setVisited] = useState(tab === "session");
  useEffect(() => { if (tab === "session") setVisited(true); }, [tab]);
  const key = JSON.stringify([taskId, attemptId]);
  const [position] = useState(() => window.history.state?.sessionPositions?.[key] as SessionPosition | undefined);
  return <Tabs data-slot="detail-tabs" value={tab} onValueChange={value => onTab(value as DetailTab)} className="min-h-0 min-w-0 flex-1 gap-0">
    <TabsList variant="line" aria-label={t("tabs.label")} className="h-8 w-full shrink-0 justify-start gap-6 border-0 px-6">
      <TabsTrigger value="overview" className="flex-none text-xs text-ink">{t("tabs.overview")}</TabsTrigger>
      <TabsTrigger value="session" className="flex-none text-xs text-ink">{t("sessionView.title")}</TabsTrigger>
    </TabsList>
    <TabsPrimitive.Content value="overview" forceMount className="flex min-h-0 min-w-0 flex-1 flex-col outline-none data-[state=inactive]:hidden">{children}</TabsPrimitive.Content>
    <TabsPrimitive.Content value="session" forceMount className="flex min-h-0 min-w-0 flex-1 flex-col outline-none data-[state=inactive]:hidden">
      {(visited || tab === "session") && <Suspense fallback={<p className="p-6 text-sm text-ink2" role="status">{t("sessionView.loading")}</p>}><SessionPanel
        client={client} taskId={taskId} attemptId={attemptId} harness={harness} active={active && tab === "session"} position={position}
        onPosition={next => window.history.replaceState({ ...window.history.state, sessionPositions: { ...window.history.state?.sessionPositions, [key]: next } }, "")}/></Suspense>}
    </TabsPrimitive.Content>
  </Tabs>;
}
