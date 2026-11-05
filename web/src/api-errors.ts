/** Convert FastAPI's error payloads into clear, bounded UI messages.
 *
 * Pydantic field validation uses an array of objects, while business rules
 * return a string or an item-indexed object for invalid batch submissions.
 * Do not echo submitted URLs, which may contain private query parameters.
 */
type ObjectValue = Record<string, unknown>;

function asObject(value: unknown): ObjectValue | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as ObjectValue
    : null;
}

function bounded(value: string): string {
  return value.trim().slice(0, 500);
}

export function apiErrorMessage(payload: unknown, httpStatus: number): string {
  const fallback = 'Request failed (HTTP ' + httpStatus + ')';
  const detail = asObject(payload)?.detail;

  if (typeof detail === 'string' && detail.trim()) {
    return bounded(detail);
  }

  if (Array.isArray(detail)) {
    const entries = detail.slice(0, 4).map(entry => {
      const item = asObject(entry);
      if (typeof item?.msg !== 'string' || !item.msg.trim()) return '';
      const parts = Array.isArray(item.loc)
        ? item.loc.filter(part => typeof part === 'string' || typeof part === 'number')
          .map(String).slice(0, 8)
        : [];
      return bounded((parts.length ? parts.join('.') + ': ' : '') + item.msg);
    }).filter(Boolean);
    if (entries.length) {
      const excess = detail.length > 4 ? ' (+' + (detail.length - 4) + ' more)' : '';
      return bounded(entries.join('; ') + excess);
    }
  }

  const item = asObject(detail);
  if (item && typeof item.error === 'string' && item.error.trim()) {
    const prefix = typeof item.item === 'number' && Number.isInteger(item.item) && item.item > 0
      ? 'Batch item ' + item.item + ': ' : '';
    return bounded(prefix + item.error);
  }

  return fallback;
}
