import codex from "@lobehub/icons-static-svg/icons/codex-color.svg";
import claude from "@lobehub/icons-static-svg/icons/claudecode-color.svg";
import deepseek from "@lobehub/icons-static-svg/icons/deepseek-color.svg";
import zai from "@lobehub/icons-static-svg/icons/zai.svg";

const logos: Record<string, string> = { codex, claude, dsh: deepseek, zcode: zai };

/** Native harness marks retain their colours; the monochrome mark follows the text. */
export function HarnessLogo({ harness }: { harness: string }) {
  const src = logos[harness];
  const slot = "block size-6 shrink-0";
  if (!src) return <span aria-hidden="true" className={slot} />;
  return harness === "zcode"
    ? <span aria-hidden="true" data-slot="harness-logo" className={`${slot} bg-current`} style={{ maskImage: `url("${src}")`, maskPosition: "center", maskSize: "contain", maskRepeat: "no-repeat" }} />
    : <img alt="" className={`${slot} object-contain`} src={src} />;
}
