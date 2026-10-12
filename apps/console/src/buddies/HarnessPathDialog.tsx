import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { absolutePath } from "./harness";

/**
 * The advanced fallback: one absolute path to the executable, or clearing it
 * back to automatic detection. Saving checks the harness at once; the check and
 * its result belong to the view, which carries the recorded revision.
 */
export function HarnessPathDialog({ adapter, name, manualPath, disabled, reason, onSave, onClose }: {
  adapter: string; name: string;
  /** The path stored now; `null` while detection is automatic. */
  manualPath: string | null;
  /** The action cannot start now. */
  disabled: boolean;
  /** Why it cannot, when it cannot be written. */
  reason: string | undefined;
  onSave: (path: string | null) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation("buddies");
  const id = useId();
  const [value, setValue] = useState(manualPath ?? "");
  const [invalid, setInvalid] = useState(false);
  // The check is the validation of this boundary: a relative path is refused here and sent nowhere.
  function submit() {
    if (disabled) return;
    const path = value.trim();
    if (!absolutePath(path)) { setInvalid(true); return; }
    onSave(path);
  }
  return <Dialog open onOpenChange={open => { if (!open) onClose(); }}>
    <DialogContent aria-describedby={undefined} className="sm:max-w-md">
      <DialogHeader><DialogTitle>{t("harness.path.title", { name })}</DialogTitle></DialogHeader>
      <form className="flex flex-col gap-4" onSubmit={event => { event.preventDefault(); submit(); }}>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={id} className="text-xs text-ink3">{t("harness.path.field")}</label>
          <Input id={id} className="h-8 font-mono text-xs shadow-none" value={value} placeholder={`/usr/local/bin/${adapter}`}
            autoComplete="off" spellCheck={false} aria-invalid={invalid || undefined}
            onChange={event => { setValue(event.target.value); setInvalid(false); }} />
          {invalid && <p className="text-xs text-fail" role="alert">{t("harness.path.invalid")}</p>}
        </div>
        <DialogFooter className="items-center sm:justify-between">
          {manualPath
            ? <Hint text={reason}>
              <Button type="button" variant="outline" aria-disabled={disabled || undefined}
                onClick={() => { if (!disabled) onSave(null); }}>{t("harness.path.restore")}</Button>
            </Hint>
            : <span />}
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{t("harness.path.cancel")}</Button>
            <Hint text={reason}>
              <Button type="submit" aria-disabled={disabled || undefined}>{t("harness.path.save")}</Button>
            </Hint>
          </div>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>;
}
