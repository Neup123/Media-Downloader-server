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
  const detail = asObject(payload)?.detail;
  const raw = typeof detail === 'string' ? detail.toLowerCase() : '';
  // Known recovery cases are actionable without surfacing HTTP status codes or
  // repeating an untrusted URL, credential or server exception in the UI.
  if (httpStatus === 409 && /files|webdav|app.password|connect/.test(raw)
      && /connect|not.configured|missing|password|credential/.test(raw)) {
    return "Your Files aren't connected yet. Connect them once in My settings, then this download will start.";
  }
  if (httpStatus === 409 && /destination|folder|directory/.test(raw)) {
    return "Couldn't reach your destination folder. Check the folder still exists, then try again.";
  }
  if (httpStatus === 401) return 'Your connection has expired or the key is incorrect. Reconnect in My settings.';
  if (httpStatus === 403) return 'You do not have access to this action. Ask your administrator to check your permissions.';
  if (httpStatus === 429) return 'Too many requests. Try again shortly.';
  if (httpStatus >= 500) return 'The downloader service could not complete this request. Try again or check its connection.';
  const fallback = httpStatus === 404
    ? 'This item could not be found. Refresh the list and try again.'
    : httpStatus === 422
      ? 'Some details were not accepted. Check the fields and try again.'
      : 'Could not complete the request. Check the details and try again.';

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
