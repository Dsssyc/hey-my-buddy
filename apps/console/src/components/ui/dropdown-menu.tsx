import type { ComponentProps } from "react";
import { DropdownMenu as Menu } from "radix-ui";
import { Check } from "lucide-react";
import { cn } from "cn";

export const DropdownMenu = Menu.Root;
export const DropdownMenuTrigger = Menu.Trigger;
export const DropdownMenuRadioGroup = Menu.RadioGroup;

export function DropdownMenuContent({ className, ...props }: ComponentProps<typeof Menu.Content>) {
  return <Menu.Portal><Menu.Content align="end" side="bottom" sideOffset={4}
    className={cn("overlay-surface z-50 min-w-44 border bg-popover p-1 text-popover-foreground shadow-md outline-none origin-(--radix-dropdown-menu-content-transform-origin) data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-1 data-[side=top]:slide-in-from-bottom-1 duration-150 motion-reduce:animate-none motion-reduce:data-[state=open]:animate-none motion-reduce:data-[state=closed]:animate-none motion-reduce:transition-none", className)} {...props}/></Menu.Portal>;
}

const itemClass = "flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none transition-colors duration-100 motion-reduce:transition-none data-[highlighted]:bg-accent data-[disabled]:opacity-50";
export function DropdownMenuItem({ className, ...props }: ComponentProps<typeof Menu.Item>) {
  return <Menu.Item className={cn(itemClass, className)} {...props}/>;
}
export function DropdownMenuRadioItem({ children, ...props }: ComponentProps<typeof Menu.RadioItem>) {
  return <Menu.RadioItem className={itemClass} {...props}>
    <span className="size-4"><Menu.ItemIndicator><Check className="size-4" aria-hidden="true"/></Menu.ItemIndicator></span>{children}
  </Menu.RadioItem>;
}
export function DropdownMenuLabel(props: ComponentProps<typeof Menu.Label>) {
  return <Menu.Label className="px-2 pt-1.5 pb-1 text-xs text-ink3" {...props}/>;
}
export function DropdownMenuSeparator() {
  return <Menu.Separator className="my-1 border-t"/>;
}
