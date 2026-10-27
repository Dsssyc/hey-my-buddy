import { lazy, Suspense } from "react";
import type { ReactNode } from "react";
import { Tabs as TabsPrimitive } from "radix-ui";
import { useTranslation } from "react-i18next";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import type { SessionClient } from "../session-view/use-session";
import type { DetailTab } from "./location";

const SessionPanel = lazy(() => import("../session-view/SessionPanel"));

/** Only the visible tab is mounted: leaving the session releases its read pages. */
export function ExecutionTabs({ routingMode, taskText, checkoutRoot, children, client, taskId, attemptId, harness, active, tab, onTab }: {
  routingMode?: "fast" | "review" | null; taskText: string; checkoutRoot: string; children: ReactNode; client: SessionClient; taskId: string; attemptId: string; harness: string;
  active: boolean; tab: DetailTab; onTab: (tab: DetailTab) => void;
}) {
  const { t } = useTranslation("taskDetail");
  return <Tabs data-slot="detail-tabs" value={tab} onValueChange={value => onTab(value as DetailTab)} className="min-h-0 min-w-0 flex-1 gap-0">
    <TabsList variant="line" aria-label={t("tabs.label")} className="h-8 w-full shrink-0 justify-start gap-6 border-0 px-6">
      <TabsTrigger value="overview" className="flex-none text-xs text-ink">{t("tabs.overview")}</TabsTrigger>
      <TabsTrigger value="session" className="flex-none text-xs text-ink">{t("sessionView.title")}</TabsTrigger>
    </TabsList>
    <TabsPrimitive.Content value="overview" forceMount className="flex min-h-0 min-w-0 flex-1 flex-col outline-none data-[state=inactive]:hidden">{children}</TabsPrimitive.Content>
    <TabsPrimitive.Content value="session" className="flex min-h-0 min-w-0 flex-1 flex-col outline-none">
      <Suspense fallback={<p className="p-6 text-sm text-ink2" role="status">{t("sessionView.loading")}</p>}><SessionPanel
        routingMode={routingMode} taskText={taskText} checkoutRoot={checkoutRoot} client={client} taskId={taskId} attemptId={attemptId} harness={harness} active={active}/></Suspense>
    </TabsPrimitive.Content>
  </Tabs>;
}
