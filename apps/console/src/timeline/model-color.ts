/** A model keeps its colour across macro tasks, providers and harnesses. */
export function modelColor(model: string | null | undefined): string | undefined {
  if (!model) return undefined;
  let hash = 0;
  for (const character of model) hash = (Math.imul(hash, 31) + character.codePointAt(0)!) | 0;
  return `var(--model-${(hash >>> 0) % 6 + 1})`;
}
