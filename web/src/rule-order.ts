/** Pure routing ordering helpers. No downloads or server calls. */
export type OrderedRule = {id: string};

export function moveRule<T extends OrderedRule>(
  rules: readonly T[], sourceId: string, targetId: string,
  placement: 'before' | 'after' = 'before',
): T[] {
  const from = rules.findIndex(item => item.id === sourceId);
  const target = rules.findIndex(item => item.id === targetId);
  if (from < 0 || target < 0 || from === target) return [...rules];
  const result = [...rules];
  const [moved] = result.splice(from, 1);
  const relocated = result.findIndex(item => item.id === targetId);
  result.splice(relocated + (placement === 'after' ? 1 : 0), 0, moved);
  return result;
}

export function stepRule<T extends OrderedRule>(
  rules: readonly T[], sourceId: string, direction: -1 | 1,
): T[] {
  const at = rules.findIndex(item => item.id === sourceId);
  if (at < 0 || at + direction < 0 || at + direction >= rules.length) return [...rules];
  const result = [...rules];
  [result[at], result[at + direction]] = [result[at + direction], result[at]];
  return result;
}

export function isReordered<T extends OrderedRule>(before: readonly T[], after: readonly T[]) {
  return before.length === after.length &&
    before.some((item, index) => item.id !== after[index].id);
}
