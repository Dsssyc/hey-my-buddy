import * as React from "react";
import { cn } from "cn";
import { UnsavedMark } from "@/components/ui/state-mark";

/**
 * One section of a page of settings: its name on the left, in the secondary
 * colour, and its content on the right; a thin line divides the sections. A mark beside the name
 * says that something in the section is changed and not saved.
 */
function Section({
  name,
  unsaved,
  className,
  children,
  ...props
}: Omit<React.ComponentProps<"div">, "title"> & {
  name: string;
  /** The words of the unsaved mark, given while the section holds a change that is not saved. */
  unsaved?: string;
}) {
  const id = React.useId();
  return (
    <div
      data-slot="section"
      role="group"
      aria-labelledby={id}
      className={cn("grid grid-cols-[112px_minmax(0,1fr)] gap-x-5 gap-y-2 border-b px-6 py-[18px] outline-none last:border-b-0 focus-visible:bg-sunk max-sm:grid-cols-1", className)}
      {...props}
    >
      <div className="flex items-start gap-1.5 pt-[5px] text-ink2">
        <span id={id}>{name}</span>
        {unsaved && <UnsavedMark label={unsaved} className="mt-[7px]" />}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export { Section };
