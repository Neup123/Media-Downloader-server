/**
 * Serializes autosaves for one engine so an older HTTP response cannot
 * overwrite a newer edit. Initial server hydration never sends a PUT.
 * No storage or network decisions live in this component-agnostic queue.
 */
export type SaveState = 'loading' | 'pending' | 'saving' | 'saved' | 'error';
type Snapshot<T> = { signature: string; value: T };

export class EngineAutosaveQueue<T> {
  private baseline: string | null = null;
  private latest: Snapshot<T> | null = null;
  private saving = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private disposed = false;

  constructor(
    private readonly persist: (value: T) => Promise<unknown>,
    private readonly status: (next: SaveState) => void,
    private readonly delayMs = 650,
  ) {}

  hydrate(value: T) {
    if (this.disposed) return;
    this.clearTimer();
    this.baseline = JSON.stringify(value);
    this.latest = null;
    this.status('saved');
  }

  change(value: T) {
    if (this.disposed || this.baseline === null) return;
    const signature = JSON.stringify(value);
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

  retry() {
    if (this.disposed || !this.latest) return;
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
    if (this.disposed || this.saving || !this.latest) return;
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
      if (!this.disposed && this.latest?.signature === saving.signature) {
        this.latest = null;
        this.status('saved');
      }
    } catch {
      failed = true;
      if (!this.disposed && this.latest?.signature === saving.signature) {
        this.status('error');
      }
    } finally {
      this.saving = false;
      if (!this.disposed && this.latest && this.latest.signature !== saving.signature) {
        this.schedule();
      } else if (!this.disposed && !failed && !this.latest) {
        this.status('saved');
      }
    }
  }
}
