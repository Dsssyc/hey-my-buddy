import { useTranslation } from "react-i18next";
import { BuddyIdentityName, BuddyName, useBuddies } from "../components/AliasTag";
import type { BuddyIdentity } from "../components/AliasTag";
import { isReason, type Reason } from "../reason";
import { reasonParameterText } from "./reason";
import { Joined, Message, MessageList } from "./Message";

function Parameter({ value, name, code }: { value: unknown; name: string; code: string }) {
  const buddies = useBuddies();
  if (isReason(value)) return <ReasonMessage reason={value} />;
  if (Array.isArray(value)) {
    if (!value.length || (name === "errors" && code === "maintenance-answer-invalid"))
      return <>{reasonParameterText(value, name, code)}</>;
    return <MessageList items={value.map(item => <Parameter value={item} name={name} code={code} />)} />;
  }
  if (value && typeof value === "object" && "model" in value) {
    const row = value as Record<string, unknown>;
    const buddy = Object.fromEntries(["harness", "provider", "model", "effort", "alias"].map(key => [key, row[key] == null ? null : String(row[key])])) as BuddyIdentity;
    return <BuddyIdentityName buddy={buddy} />;
  }
  if (name === "buddyId" && typeof value === "string") {
    const buddy = buddies.find(row => row.buddyId === value);
    if (buddy) return <BuddyName buddy={buddy}>{value}</BuddyName>;
  }
  return <>{reasonParameterText(value, name, code)}</>;
}

/** Program reasons share their existing catalog; Trans places each React parameter. */
export function ReasonMessage({ reason }: { reason: Reason }) {
  const { t, i18n } = useTranslation("shell");
  const template = i18n.getResource(i18n.resolvedLanguage!, "reasons", reason.code, { keySeparator: false }) as string;
  const values = Object.fromEntries(Object.entries(reason.params).map(([name, value]) => [name, <Parameter value={value} name={name} code={reason.code} />]));
  const facts = Object.entries(values).filter(([name]) => !template.includes(`{{${name}}}`))
    .map(([name, value]) => <Message ns="shell" id="reasonFact" values={{ name: i18n.t(`shell:reasonParam.${name}`), value }} />);
  return <Joined separator=" " items={[<Message ns="reasons" id={reason.code} literalKey values={values} />, ...facts]} />;
}

export function ReasonMessages({ reasons, harnessSaid = false }: { reasons: Reason[]; harnessSaid?: boolean }) {
  const distinct = [...new Map(reasons.filter(reason => !harnessSaid || !reason.code.startsWith("harness."))
    .map(reason => [JSON.stringify(reason), reason])).values()];
  return <Joined items={distinct.map(reason => <ReasonMessage reason={reason} />)} />;
}
