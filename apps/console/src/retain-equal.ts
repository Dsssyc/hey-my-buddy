/** Share unchanged parts of JSON read models across successful reads. */
export function retainEqual<T>(previous: T, next: T): T {
  if (Object.is(previous, next)) return previous;
  if (previous === null || next === null || typeof previous !== "object" || typeof next !== "object" || Array.isArray(previous) !== Array.isArray(next)) return next;
  const before = previous as Record<string, unknown>, after = next as Record<string, unknown>;
  const keys = Object.keys(after);
  let equal = Object.keys(before).length === keys.length;
  const shared: Record<string, unknown> = Array.isArray(next) ? [...next] as unknown as Record<string, unknown> : { ...after };
  for (const key of keys) {
    shared[key] = retainEqual(before[key], after[key]);
    if (!Object.hasOwn(before, key) || shared[key] !== before[key]) equal = false;
  }
  return equal ? previous : shared as T;
}
