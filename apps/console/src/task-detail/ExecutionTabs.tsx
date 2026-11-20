import { lazy, Suspense, useState } from "react";
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
  // The session panel supplies the summary and coverage at the end of the tab row.
  const [tabsSide, setTabsSide] = useState<HTMLSpanElement | null>(null);
  return <Tabs data-slot="detail-tabs" value={tab} onValueChange={value => onTab(value as DetailTab)} className="min-h-0 min-w-0 flex-1 gap-0">
    <TabsList variant="line" aria-label={t("tabs.label")} className="h-[34px] w-full shrink-0 justify-start gap-6 border-0 border-b border-line2 px-6 group-data-[orientation=horizontal]/tabs:h-[34px] @max-[440px]:gap-3">
      <TabsTrigger value="overview" className="flex-none text-xs text-ink group-data-[variant=line]/tabs-list:h-full">{t("tabs.overview")}</TabsTrigger>
      <TabsTrigger value="session" className="flex-none text-xs text-ink group-data-[variant=line]/tabs-list:h-full">{t("sessionView.title")}</TabsTrigger>
      <span ref={setTabsSide} data-slot="detail-tabs-side" className="ml-auto inline-flex min-w-0 items-center gap-2"/>
    </TabsList>
    <TabsPrimitive.Content value="overview" forceMount className="view-presence flex min-h-0 min-w-0 flex-1 flex-col outline-none data-[state=inactive]:hidden">{children}</TabsPrimitive.Content>
    <TabsPrimitive.Content value="session" className="view-presence flex min-h-0 min-w-0 flex-1 flex-col pt-4 outline-none">
      <Suspense fallback={<p className="p-6 text-sm text-ink2" role="status">{t("sessionView.loading")}</p>}><SessionPanel
        routingMode={routingMode} taskText={taskText} checkoutRoot={checkoutRoot} client={client} taskId={taskId} attemptId={attemptId} harness={harness} active={active} tabsSide={tabsSide}/></Suspense>
    </TabsPrimitive.Content>
  </Tabs>;
}
