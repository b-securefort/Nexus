# Drill 3 — KB hybrid retrieval

**Diagram**: [`backend/output/nexus-drill3-kb-hybrid-retrieval.drawio`](../../backend/output/nexus-drill3-kb-hybrid-retrieval.drawio) · [PNG preview](../../backend/output/nexus-drill3-kb-hybrid-retrieval.png)

**Audience**: Engineers working on the KB, anyone debugging retrieval quality, anyone asking "why don't I see file X in search results".

**Time to present**: ~6 minutes.

---

## TL;DR

Two pipelines that meet at three SQLite virtual tables. **Ingest** (left): Git pull → markdown normalize → chunk at H2/H3 → Azure OpenAI embed → `kb_chunks` + `kb_chunks_fts` + `kb_chunks_vec`. **Query** (right): `search_kb_hybrid` tool call → AOAI embed query side → parallel BM25 + vector stages → Reciprocal Rank Fusion → top-K chunks with `source_url` citations.

---

## Teleprompter script

> **Set up the frame.**
> "This is the diagram I'm asked about the most. 'How does Nexus find content in the KB?' Two pipelines, meeting at the same SQLite database. Ingest on the left, query on the right."

> **Walk the ingest pipeline (steps 1–6).**
> "Step 1: every 15 minutes — that interval is `KB_SYNC_INTERVAL_SECONDS`, default 900 — we `git pull` the KB repo. The repo can be ADO, GitHub, or any Git host. It's the team's source of truth for documentation.
>
> Step 2: we normalize the markdown. That means: validate that every file has YAML front-matter with `source_url`, `last_synced`, `source`, and `original_path`. Anything that's not `.md` we ignore for chunking; curated entries in `kb_index.json` can still reference them but the auto-scanner is `.md`-only.
>
> Step 3: the Chunker. It splits at H2 and H3 boundaries, capping chunk size at `KB_CHUNK_MAX_CHARS`. Each chunk gets a heading breadcrumb — like 'Guide > Installation > Windows' — so the agent knows where in the doc it came from.
>
> Step 4: Azure OpenAI embed, document side. Every chunk goes through `text-embedding-3-small`. That returns a vector of 1536 floats. We L2-normalize it because cosine similarity in vec0 needs unit vectors. This is one Azure OpenAI call per chunk, batched. Initial corpus indexing costs around 15 cents one-time.
>
> Step 5 and 6: the vectors and the chunk metadata land in three tables together: `kb_chunks` is the canonical row — path, chunk_idx, heading, text, content_hash, source_url, embed_model. `kb_chunks_fts` is a FTS5 virtual table, kept in sync by triggers. `kb_chunks_vec` is a vec0 virtual table — sqlite-vec extension — joined by rowid, holding `float[1536]`. The reindexer writes to `kb_chunks` and `kb_chunks_vec` explicitly; FTS5 self-syncs via triggers."

> **Why these three tables — call out the design choice.**
> "Why not just one table with a BLOB column for the embedding? Because nearest-neighbour search on a BLOB column is a full table scan + cosine in Python. vec0 owns its own ANN-friendly layout, supports `MATCH '[...]' ORDER BY distance` natively, and we use that in the query path."

> **Walk the query pipeline (A–F).**
> "The agent calls `search_kb_hybrid` as a tool. That's the box top-right. The tool gets one argument: the query text.
>
> Step A: query text in. Step B: Azure OpenAI embed, query side — one more 1536-dim vector. That call takes about 50ms.
>
> In parallel — and this is the 'hybrid' in hybrid retrieval — two ranking stages fire:
>
> The **BM25 stage** runs `FTS5 MATCH` against `kb_chunks_fts`. BM25 is the classic keyword-relevance algorithm; it scores chunks high when query terms appear with the right frequency and density. Tokenizer is `unicode61` with diacritic folding, *no* porter stemming. Why no stemming? Because 'kubernetes' shouldn't become 'kubernet'. Technical jargon is exactly the wrong place to stem.
>
> The **Vector stage** runs `vec0 MATCH ORDER BY distance` against `kb_chunks_vec`. That's cosine similarity over the 1536-dim space. It catches relevance even when the query and chunk share zero keywords — like 'prevent lateral movement after host compromise' returning the 'Zero Trust' chapter.
>
> Both stages return a ranked list — top 50 each, typically.
>
> Step F: **Reciprocal Rank Fusion** — `_rrf_fuse`. Each result in each list gets a score of `1 / (rank + 60)`. We sum scores across lists. Chunks ranking high in *both* lists rise to the top. Output: top-K chunks, each with its `source_url` cite so the agent can attribute its answer."

> **Why hybrid, not just one stage.**
> "Two complementary failure modes. BM25 fails when the user uses different vocabulary than the doc. Vector fails when there's exact terminology that matters — like a specific resource name. RRF fuses them and you get the best of both. We A/B tested this against the cloud-only `search_kb_semantic` path; both agreed on top-1 across the golden set."

> **Close.**
> "Two more things. First: WAL mode is on, so the reindexer can write while chat reads are happening. Second: there's an `embed_model` column on `kb_chunks` — if we ever swap models, the reindexer auto-detects the mismatch and forces a full re-embed. We don't have to remember. Questions?"

---

## Appendix A — What each node is and why it's there

| Node | What it is | Why it's in the diagram |
|---|---|---|
| **KB Git repo (ADO / GitHub)** | The team's documentation source, hosted on any Git provider. | The KB content origin. Nexus never queries this directly — only the local synced copy. |
| **git sync (15m) + normalize markdown** | [`backend/app/kb/git_sync.py`](../../backend/app/kb/git_sync.py) + the ingestion normalizer. Runs every 15 minutes. | Periodic pull + front-matter normalization. Where ADO wikis and PDF link-lists get converted to consistent markdown. |
| **Chunker (split at H2/H3)** | [`backend/app/kb/chunker.py`](../../backend/app/kb/chunker.py). Respects `KB_CHUNK_MAX_CHARS`. | Chunks are the retrieval unit — files are too coarse. H2/H3 boundaries give semantically meaningful chunks. |
| **Azure OpenAI embed (document side)** | `text-embedding-3-small` deployment, 1536-dim output, L2-normalized. | The "semantic" half of hybrid retrieval. Document-side encoding happens at index time. |
| **kb_chunks (canonical)** | Real SQLite table. Columns: path, chunk_idx, heading, text, content_hash, file_mtime, source_url, embed_model. | The single source of truth row. Everything else references this rowid. `embed_model` records which model produced the vector — drives re-embed when models swap. |
| **kb_chunks_fts (FTS5 virtual, unicode61 no porter)** | FTS5 virtual table over the canonical's `text + heading`. Kept in sync by INSERT/UPDATE/DELETE triggers. | The keyword-search half of hybrid retrieval. `unicode61 no porter` is deliberate — see the design log. |
| **kb_chunks_vec (vec0 virtual, float[1536])** | sqlite-vec virtual table, rowid-joined to canonical. Holds the L2-normalized 1536-dim vectors. | The vector-search half of hybrid retrieval. Owns its own ANN layout — far faster than a BLOB column + Python loop. |
| **search_kb_hybrid (tool call from agent)** | [`backend/app/tools/generic/kb_tools.py`](../../backend/app/tools/generic/kb_tools.py). The agent-facing tool. | Single entry point for the query path. Takes a query, returns top-K chunks with `source_url`. |
| **Azure OpenAI embed (query side, ~50 ms)** | Same model deployment as the document side. | The query needs to be in the same 1536-dim space as the documents. Cost: one embed call per search. |
| **BM25 stage (FTS5 MATCH)** | The keyword-search call: `SELECT ... FROM kb_chunks_fts WHERE kb_chunks_fts MATCH ?`. | Classic keyword relevance. Catches matches where exact terminology matters. |
| **Vector stage (vec0 MATCH ORDER BY distance)** | sqlite-vec query: `SELECT ... FROM kb_chunks_vec WHERE embedding MATCH ? ORDER BY distance`. | Semantic similarity. Catches relevance even with zero keyword overlap. |
| **Reciprocal Rank Fusion (_rrf_fuse)** | The fusion function in retrieval.py. `score = sum(1 / (rank + K))` per result, K=60. | Combines two ranked lists into one without needing scores on the same scale. Chunks ranked high by both win. |
| **Top-K chunks + source_url cite** | The final ranked list of chunks the agent receives, each with a `source_url` for citation. | The output. The agent uses these chunks in its response and quotes their `source_url` so the user can verify. |

---

## Appendix B — Edges (the lines)

**Ingest path:**

| Step | From → To | Label | Meaning |
|---|---|---|---|
| 1 | KB Git repo → git sync | `1 pull` | Periodic git pull, every 15 minutes. |
| 2 | git sync → Chunker | `2 changed *.md` | Only files whose `content_hash` changed get re-chunked. |
| 3 | Chunker → AOAI embed | `3 chunk text` | Each chunk goes through the embedder document-side. |
| 4 | AOAI embed → kb_chunks | `4 vector` | Vector + chunk metadata land in the canonical table. |
| 5 | kb_chunks → kb_chunks_fts (**dashed**) | `5 trigger` | FTS5 trigger auto-syncs text + heading on every write. |
| 6 | kb_chunks → kb_chunks_vec (**dashed**) | `6 vec0 insert` | The reindexer explicitly writes to vec0 because triggers can't compute embeddings. |

**Query path:**

| Step | From → To | Label | Meaning |
|---|---|---|---|
| A | search_kb_hybrid → AOAI embed | `A query text` | The query text is embedded. |
| B | AOAI embed → Vector stage | `B vec` | The 1536-dim query vector goes into the vector search. |
| C | search_kb_hybrid → BM25 stage | `C tokens` | The same query text (no embed needed) goes into BM25. |
| D | BM25 stage → RRF | `D rank list` | BM25 returns a ranked list. |
| E | Vector stage → RRF | `E rank list` | Vector returns a ranked list. |
| F | RRF → Top-K chunks | `F fused` | RRF fuses both lists into the final ranking. |

---

## Appendix C — Glossary references

For abbreviations (BM25, RRF, FTS5, vec0, WAL, AOAI), see **[GLOSSARY.md](GLOSSARY.md)** in this folder.

For Nexus-specific terms (KB, KB chunk, KB source, Embedding, Embedding model, Reindexer, Ingestion source type, Front-matter), see the main **[GLOSSARY.md](../GLOSSARY.md)**.

For the underlying design decisions:
- Local hybrid retrieval over qmd → [DESIGN.md §5 2026-05-14](../DESIGN.md)
- `unicode61 remove_diacritics 2`, no porter stemming → [DESIGN.md §5 2026-05-15](../DESIGN.md)
- `embed_model` column on `kb_chunks` → [DESIGN.md §5 2026-05-15](../DESIGN.md)
- `kb_chunks_vec` is a vec0 virtual table (not BLOB) → [DESIGN.md §5 2026-05-15](../DESIGN.md)
- Azure OpenAI text-embedding-3-small, no local ONNX → [DESIGN.md §5 2026-05-15](../DESIGN.md)
- Golden-set A/B vs cloud semantic → [DESIGN.md §5 2026-05-16](../DESIGN.md)
- `kb_index.json` is optional metadata → [DESIGN.md §5 2026-05-20](../DESIGN.md)
