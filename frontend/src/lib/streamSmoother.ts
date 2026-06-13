/** Client-side pacing buffer for SSE token streams.
 *
 *  The backend relays model chunks as they arrive, which (after network
 *  coalescing) reads as jarring multi-sentence bursts. StreamSmoother queues
 *  incoming chunks and drains them word-by-word on a fixed interval so the
 *  text appears at a steady, readable cadence.
 *
 *  Pacing must never hide real latency: when the backlog grows (model is
 *  producing faster than the drain rate), each tick emits proportionally more
 *  words so the display converges on the live stream instead of drifting
 *  seconds behind. `flush()` synchronously emits everything pending — callers
 *  must flush before any event whose on-screen order matters (tool calls,
 *  approval cards, turn end).
 */

// Leading whitespace + one word. Whitespace travels with the word that
// follows it so markdown structure (newlines, indentation) is preserved.
const WORD_RE = /^\s*\S+/;

export class StreamSmoother {
  private queue = "";
  private timer: number | null = null;

  constructor(
    private emit: (text: string) => void,
    private intervalMs = 30,
    /** Backlog size (chars) that adds one extra word per tick. */
    private catchupChars = 240,
  ) {}

  push(text: string) {
    if (!text) return;
    const wasIdle = this.timer === null;
    this.queue += text;
    if (wasIdle) {
      // Emit the first word synchronously — time-to-first-word matters more
      // for perceived responsiveness than perfectly even pacing.
      this.tick();
      if (this.queue && this.timer === null) {
        this.timer = window.setInterval(() => this.tick(), this.intervalMs);
      }
    }
  }

  /** Synchronously emit everything still queued and stop the timer. */
  flush() {
    this.stopTimer();
    if (this.queue) {
      this.emit(this.queue);
      this.queue = "";
    }
  }

  /** Discard everything still queued (stream aborted) and stop the timer. */
  cancel() {
    this.stopTimer();
    this.queue = "";
  }

  private stopTimer() {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private tick() {
    if (!this.queue) {
      this.stopTimer();
      return;
    }
    const words = 1 + Math.floor(this.queue.length / this.catchupChars);
    let out = "";
    for (let i = 0; i < words && this.queue; i++) {
      const m = WORD_RE.exec(this.queue);
      if (!m) {
        // Only whitespace left — emit it as-is.
        out += this.queue;
        this.queue = "";
        break;
      }
      out += m[0];
      this.queue = this.queue.slice(m[0].length);
    }
    if (out) this.emit(out);
    if (!this.queue) this.stopTimer();
  }
}
