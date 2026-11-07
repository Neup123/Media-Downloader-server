/**
 * Serializes autosaves for one settings resource so an older HTTP response cannot
 * overwrite a newer edit. Initial server hydration never sends a PUT.
 * No storage or network decisions live in this component-agnostic queue.
 */
export type SaveState = 'loading' | 'pending' | 'saving' | 'saved' | 'error';
type Snapshot<T> = { signature: string; value: T };

/**
 * Compare JSON settings by meaning, not insertion order of object properties.
 * Hydrated API payloads and UI form drafts often enumerate the same fields in
 * different orders. Array ordering remains significant for playlist/settings.
 */
function stableSignature(value: unknown): string {
  return JSON.stringify(value, (_key, entry: unknown) => {
    if (entry !== null && typeof entry === 'object' && !Array.isArray(entry)) {
      return Object.fromEntries(
        Object.entries(entry).sort(([left], [right]) =>
          left < right ? -1 : left > right ? 1 : 0),
      );
    }
    return entry;
  });
}

export class EngineAutosaveQueue<T> {
  private baseline: string | null = null;
  private latest: Snapshot<T> | null = null;
  private saving = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private disposed = false;
  private paused = false;

  private readonly persist: (value: T) => Promise<unknown>;
  private readonly status: (next: SaveState) => void;
  private readonly delayMs: number;

  constructor(
    persist: (value: T) => Promise<unknown>,
    status: (next: SaveState) => void,
    delayMs = 650,
  ) {
    this.persist = persist;
    this.status = status;
    this.delayMs = delayMs;
  }

  hydrate(value: T) {
    if (this.disposed) return;
    this.clearTimer();
    this.paused = false;
    this.baseline = stableSignature(value);
    this.latest = null;
    this.status('saved');
  }

  change(value: T) {
    if (this.disposed || this.baseline === null) return;
    this.paused = false;
    const signature = stableSignature(value);
    if (signature === this.baseline && !this.saving) {
      this.clearTimer();
      this.latest = null;
      this.status('saved');
      return;
    }
    this.latest = {signature, value};
    this.status('pending');
    this.schedule();
  }

  pause() {
    if (this.disposed) return;
    this.paused = true;
    this.clearTimer();
    this.latest = null;
    this.status('error');
  }

  retry() {
    if (this.disposed || this.paused || !this.latest) return;
    this.clearTimer();
    void this.flush();
  }

  dispose() {
    this.disposed = true;
    this.clearTimer();
    this.latest = null;
  }

  private clearTimer() {
    if (this.timer !== undefined) {
      clearTimeout(this.timer);
      this.timer = undefined;
    }
  }

  private schedule() {
    this.clearTimer();
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.flush();
    }, this.delayMs);
  }

  private async flush() {
    if (this.disposed || this.paused || this.saving || !this.latest) return;
    const saving = this.latest;
    if (saving.signature === this.baseline) {
      this.latest = null;
      this.status('saved');
      return;
    }
    this.saving = true;
    this.status('saving');
    let failed = false;
    try {
      await this.persist(saving.value);
      this.baseline = saving.signature;
      if (!this.disposed && !this.paused && this.latest?.signature === saving.signature) {
        this.latest = null;
        this.status('saved');
      }
    } catch {
      failed = true;
      if (!this.disposed && !this.paused && this.latest?.signature === saving.signature) {
        this.status('error');
      }
    } finally {
      this.saving = false;
      if (!this.disposed && this.latest && this.latest.signature !== saving.signature) {
        this.schedule();
      } else if (!this.disposed && !this.paused && !failed && !this.latest) {
        this.status('saved');
      }
    }
  }
}
