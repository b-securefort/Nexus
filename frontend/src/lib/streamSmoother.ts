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
  private readonly emit: (text: string) => void;
  private readonly intervalMs: number;
  /** Backlog size (chars) that adds one extra word per tick. */
  private readonly catchupChars: number;
  /** Hard ceiling on words emitted per tick. Without it, a reply delivered in
   *  big network-coalesced bursts reveals as multi-word slabs (reads as
   *  flicker); the cap keeps the reveal even and lets `flush()` on turn-end
   *  absorb any residual backlog in one go instead. */
  private readonly maxWordsPerTick: number;
  /** Fast start: the opening of each turn is revealed immediately (unpaced) up
   *  to this many characters, then draining switches to paced word-by-word.
   *  Without it the first sentence — which the backend often delivers as a
   *  rapid burst of small SSE events — would drip out at the paced rate and
   *  make the response feel slow to begin. Set 0 to disable (pace from char
   *  one). Reset per turn in `cancel()`. */
  private readonly primeChars: number;
  private primeRemaining: number;

  // Parameter properties (`private x` in the signature) are disallowed here
  // by `erasableSyntaxOnly`, so fields are declared above and assigned here.
  constructor(
    emit: (text: string) => void,
    intervalMs = 110,
    catchupChars = 280,
    maxWordsPerTick = 3,
    primeChars = 200,
  ) {
    this.emit = emit;
    this.intervalMs = intervalMs;
    this.catchupChars = catchupChars;
    this.maxWordsPerTick = maxWordsPerTick;
    this.primeChars = primeChars;
    this.primeRemaining = primeChars;
  }

  push(text: string) {
    if (!text) return;
    this.queue += text;
    // Fast start: while the turn's prime budget remains, reveal text the moment
    // it arrives (network-speed, unpaced) so the opening doesn't drip. Pacing
    // begins once the budget is spent. Overshoot is fine — prime is a floor on
    // how much to reveal fast, not a hard cap.
    if (this.primeRemaining > 0) {
      const out = this.queue;
      this.queue = "";
      this.primeRemaining -= out.length;
      this.emit(out);
      return;
    }
    const wasIdle = this.timer === null;
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

  /** Discard everything still queued (stream aborted) and stop the timer. Also
   *  rearms the fast-start budget — callers invoke this at the start of each
   *  turn, so the next turn's opening reveals fast again. */
  cancel() {
    this.stopTimer();
    this.queue = "";
    this.primeRemaining = this.primeChars;
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
    const words = Math.min(
      this.maxWordsPerTick,
      1 + Math.floor(this.queue.length / this.catchupChars),
    );
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
