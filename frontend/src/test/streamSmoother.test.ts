import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { StreamSmoother } from "../lib/streamSmoother";

// Pacing tests disable the fast-start prime budget (5th arg = 0) so they
// exercise word-by-word draining from the first character. Fast start is
// covered separately in its own block below.
const paced = (emit: (t: string) => void, intervalMs = 30, catchupChars = 280, maxWords = 3) =>
  new StreamSmoother(emit, intervalMs, catchupChars, maxWords, 0);

describe("StreamSmoother (paced)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("emits the first word synchronously on push", () => {
    const out: string[] = [];
    const s = paced((t) => out.push(t));
    s.push("hello world there");
    // First word lands immediately, before any timer fires.
    expect(out.join("")).toBe("hello");
  });

  it("drains remaining words one per interval", () => {
    const out: string[] = [];
    const s = paced((t) => out.push(t));
    s.push("alpha beta gamma");
    expect(out.join("")).toBe("alpha");
    vi.advanceTimersByTime(30);
    expect(out.join("")).toBe("alpha beta");
    vi.advanceTimersByTime(30);
    expect(out.join("")).toBe("alpha beta gamma");
  });

  it("preserves leading whitespace with each word", () => {
    const out: string[] = [];
    const s = paced((t) => out.push(t));
    s.push("line1\n\n- item");
    vi.advanceTimersByTime(120);
    s.flush();
    expect(out.join("")).toBe("line1\n\n- item");
  });

  it("speeds up when the backlog grows (catch-up)", () => {
    const out: string[] = [];
    // catchupChars=10 → a backlog of ~15 chars emits >1 word per tick, so a
    // single tick advances by more than one word (vs. exactly one when idle).
    const s = paced((t) => out.push(t), 30, 10);
    s.push("aa bb cc dd ee ff"); // "aa" emitted synchronously
    const wordsBefore = out.join("").trim().split(/\s+/).length;
    vi.advanceTimersByTime(30);
    const wordsAfter = out.join("").trim().split(/\s+/).length;
    expect(wordsAfter - wordsBefore).toBeGreaterThan(1);
  });

  it("never emits more than maxWordsPerTick, even with a huge backlog", () => {
    const out: string[] = [];
    // Tiny catchupChars + huge backlog would request many words/tick; the cap
    // (2 here) holds the per-tick reveal even so it doesn't read as flicker.
    const s = paced((t) => out.push(t), 30, 1, 2);
    s.push("aa bb cc dd ee ff gg hh ii jj");
    // Synchronous first tick is itself capped at 2 words.
    expect(out.join("").trim().split(/\s+/).length).toBeLessThanOrEqual(2);
    out.length = 0;
    vi.advanceTimersByTime(30);
    // Each subsequent tick also emits at most 2 words.
    expect(out.join("").trim().split(/\s+/).length).toBeLessThanOrEqual(2);
  });

  it("flush emits everything pending synchronously and stops", () => {
    const out: string[] = [];
    const s = paced((t) => out.push(t));
    s.push("one two three four");
    s.flush();
    expect(out.join("")).toBe("one two three four");
    // No further output after flush.
    vi.advanceTimersByTime(300);
    expect(out.join("")).toBe("one two three four");
  });

  it("cancel discards queued text", () => {
    const out: string[] = [];
    const s = paced((t) => out.push(t));
    s.push("keep dropme dropme");
    expect(out.join("")).toBe("keep");
    s.cancel();
    vi.advanceTimersByTime(300);
    expect(out.join("")).toBe("keep");
  });

  it("resumes the timer when pushed again after draining", () => {
    const out: string[] = [];
    const s = paced((t) => out.push(t));
    s.push("first");
    vi.advanceTimersByTime(300); // drains, timer stops
    s.push(" second third");
    expect(out.join("")).toBe("first second");
    vi.advanceTimersByTime(30);
    expect(out.join("")).toBe("first second third");
  });
});

describe("StreamSmoother (fast start)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("reveals the opening burst immediately, unpaced, within the prime budget", () => {
    const out: string[] = [];
    // primeChars=20: the first ~20 chars are emitted on arrival, no timer.
    const s = new StreamSmoother((t) => out.push(t), 30, 280, 3, 20);
    // A network burst dispatches several small pushes synchronously.
    s.push("Hello ");
    s.push("there, ");
    s.push("how ");
    expect(out.join("")).toBe("Hello there, how ");
  });

  it("switches to paced draining once the prime budget is spent", () => {
    const out: string[] = [];
    const s = new StreamSmoother((t) => out.push(t), 30, 280, 3, 6);
    s.push("aaaaaa ");    // 7 chars — exceeds the 6-char budget, unpaced
    expect(out.join("")).toBe("aaaaaa ");
    // Budget now spent → subsequent text is paced word-by-word.
    s.push("bb cc dd");
    expect(out.join("")).toBe("aaaaaa bb"); // only first word emitted synchronously
    vi.advanceTimersByTime(30);
    expect(out.join("")).toBe("aaaaaa bb cc");
  });

  it("rearms the prime budget on cancel (per-turn fast start)", () => {
    const out: string[] = [];
    const s = new StreamSmoother((t) => out.push(t), 30, 280, 3, 10);
    s.push("first turn text here"); // spends budget, paced after
    out.length = 0;
    s.cancel(); // new turn begins
    s.push("second ");
    s.push("turn ");
    // Budget rearmed → opening of the new turn is unpaced again.
    expect(out.join("")).toBe("second turn ");
  });

  it("a short reply within the budget appears instantly (no pacing delay)", () => {
    const out: string[] = [];
    const s = new StreamSmoother((t) => out.push(t), 30, 280, 3, 200);
    s.push("Done.");
    s.flush(); // turn ends
    expect(out.join("")).toBe("Done.");
    // Nothing was left to drip out over time.
    vi.advanceTimersByTime(300);
    expect(out.join("")).toBe("Done.");
  });
});
