# Decision Log Format — Nexus

Decision log entries live in **`Documentation/DESIGN.md` §5 Decision log**, not as separate files.
This is consistent with the 2026-05-15 decision: "Living DESIGN.md is the source of truth."

---

## When to write a decision log entry

All three must be true. If any one is missing, a PR description or code comment is enough.

1. **Hard to reverse** — touches DB schema, SSE event protocol, tool registry contract,
   auth flow, skill loading path, or the `output/` sandbox boundary.
2. **Surprising without context** — a new engineer would re-debate the decision in
   6 months if there's no record of why it was made.
3. **Real trade-off** — at least one concrete alternative was considered and rejected.

---

## Format

Append to the bottom of §5 in `Documentation/DESIGN.md`:

```markdown
### YYYY-MM-DD — Short imperative title (≤ 8 words)

One sentence on what was decided. One or two sentences on the most important why.
**Trade-off**: what was accepted or given up. If an earlier decision is superseded,
start with: "**Replaces the YYYY-MM-DD '...' decision.**"
```

Maximum 6 sentences total. If it needs more, the decision has multiple concerns — split it.

---

## What NOT to record as a decision log entry

- Refactors with no behavioural change
- Bug fixes
- Dependency version bumps (unless a major API break was involved)
- Term clarifications that belong in `Documentation/GLOSSARY.md` instead
- Decisions that are easily reversed with a one-line config change

---

## Examples from this codebase

Good (all three criteria met):
- *"2026-05-15 — User-identity ARM token passthrough via X-ARM-Token header"*
  Hard to reverse (auth flow), surprising without context (why not OBO?), real trade-off (OBO vs header).

Doesn't qualify:
- *"Fixed the timeout normalisation in run_shell"* — bug fix, easily reversed, no trade-off.
- *"Added search_kb_semantic fallback hint to read_kb_file description"* — one-line change, not surprising.
