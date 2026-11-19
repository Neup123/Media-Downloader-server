/** An empty selector value means "use the engine's default", not an empty setting.
 * Covers global settings and per-job overrides; preserves all other fields.
 */
export function setOptionOrDefault<T extends object>(
  current: T, key: string, selected: string, parsed: unknown,
): T {
  const updated = { ...current } as Record<string, unknown>;
  if (selected === '') delete updated[key];
  else updated[key] = parsed;
  return updated as T;
}
