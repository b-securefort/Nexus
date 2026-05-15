# GLOSSARY.md Format — Nexus

The Nexus domain glossary lives at **`Documentation/GLOSSARY.md`**.
This file explains how to add or update entries in it.

---

## Purpose

`GLOSSARY.md` is a **glossary only** — not a spec, not implementation guidance.
It answers: "When someone in a PR or conversation says X, what do they mean in Nexus?"

Only Nexus-specific or overloaded terms belong. Exclude general programming concepts
(async, REST, middleware, timeout). If a term means the same thing everywhere, don't add it.

---

## Adding a new term

Add a row to the **Language** table:

```markdown
| **Term** | One tight sentence: what it IS, not what it does. | Aliases to avoid |
```

Rules:
- Definition is one sentence. If you need two, the term has two meanings — split it.
- "Aliases to avoid" are the wrong words people reach for. List at least one.
- Be opinionated: if two synonyms exist, pick the canonical one and list the other as an alias.

---

## Adding a relationship

Add a bullet to the **Relationships** section:

```markdown
- One **TermA** → many **TermB**
```

Use cardinality (one-to-one, one-to-many). Only add a relationship if it's
non-obvious or frequently confused — not every association needs to be listed.

---

## Flagged ambiguities

If a term is genuinely ambiguous across the codebase (people use it to mean two
different things), add a row to the **Flagged ambiguities** table:

```markdown
| Ambiguous term | One sentence resolving which meaning wins and when. |
```

---

## When to update GLOSSARY.md

- When a new canonical term is agreed during a design discussion or code review.
- When a PR introduces a new concept that didn't exist before.
- When the same term is used inconsistently across two or more PRs.

Do it in the same PR as the code change. `GLOSSARY.md` goes stale fast if updates are batched.

---

## When NOT to update GLOSSARY.md

- To document implementation details (use code comments or DESIGN.md).
- To document a decision (use DESIGN.md §5).
- To rename a term mid-PR without team agreement — resolve in the PR discussion first.
