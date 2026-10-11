import { cn } from "cn";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { Profile, RoutingMode } from "../types";
import { buddyQuad, routerCandidates } from "./roles";

/** The buddies that can be added, in the words of the four parts; the search keeps only those that contain what is typed. */
function Choices({ profiles, list, mode, onAdd }: {
  profiles: Profile[]; list: string[]; mode: RoutingMode; onAdd: (profileId: string) => void;
}) {
  const { t } = useTranslation("buddies");
  const [query, setQuery] = useState("");
  const candidates = routerCandidates(profiles, list, mode, query);
  const none = candidates.length === 0 && !query.trim();
  return <>
    <Input type="search" className="h-8 bg-card shadow-none" aria-label={t("roles.add.search")} placeholder={t("roles.add.search")}
      value={query} onChange={event => setQuery(event.target.value)} />
    <ScrollArea className="h-[300px] border">
      {candidates.length === 0
        ? <p className="px-3 py-8 text-center text-ink3" role="status">{t(none ? "roles.add.none" : "roles.add.noMatch")}</p>
        : <ul>{candidates.map(({ profile, refusal }) => <li key={profile.profileId} className="border-b last:border-b-0">
          {/* A buddy that cannot serve stays in the list, with the reason; choosing it does nothing. */}
          <button type="button" aria-disabled={!!refusal || undefined}
            className={cn("flex w-full flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 py-2 text-left outline-none focus-visible:bg-sunk",
              refusal ? "cursor-not-allowed" : "hover:bg-sunk")}
            onClick={() => { if (!refusal) onAdd(profile.profileId); }}>
            <span className={cn("font-mono text-xs", refusal && "text-ink3")}>{buddyQuad(profile)}</span>
            {refusal && <span className="text-xs text-ink3">{refusal}</span>}
          </button>
        </li>)}</ul>}
    </ScrollArea>
  </>;
}

/**
 * The catalogue's buddies that the Router list does not have yet. Those that
 * can serve in the default mode come first; the others are listed with the
 * reason, and choosing one changes nothing. Choosing one that can serve adds it
 * at the end of the list and closes the dialog.
 */
export function RouterAddDialog({ open, onOpenChange, profiles, list, mode, onAdd }: {
  open: boolean; onOpenChange: (open: boolean) => void;
  profiles: Profile[]; list: string[]; mode: RoutingMode; onAdd: (profileId: string) => void;
}) {
  const { t } = useTranslation("buddies");
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent aria-describedby={undefined} className="gap-3 sm:max-w-[560px]">
      <DialogTitle>{t("roles.add.title")}</DialogTitle>
      <Choices profiles={profiles} list={list} mode={mode} onAdd={profileId => { onAdd(profileId); onOpenChange(false); }} />
      <DialogFooter>
        <DialogClose asChild><Button variant="outline">{t("roles.add.cancel")}</Button></DialogClose>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
