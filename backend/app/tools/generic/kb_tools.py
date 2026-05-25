"""
KB tools — read_kb_file, search_kb, search_kb_semantic, search_kb_hybrid.
"""

import json
import logging

from openai import AzureOpenAI

from app.auth.models import User
from app.kb.service import get_kb_service
from app.tools.base import Tool

logger = logging.getLogger(__name__)


class ReadKBFileTool(Tool):
    name = "read_kb_file"
    description = (
        "Read the full contents of a file from the knowledge base. "
        "Path must be relative to the KB root (e.g. 'kb/adrs/adr-001.md'). "
        "Use search_kb or search_kb_semantic first to discover valid paths — "
        "guessing a path will return 'File not found'."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file within the KB, e.g. 'kb/adrs/adr-001.md'",
            }
        },
        "required": ["path"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        if not isinstance(args, dict):
            return "Error: invalid arguments"
        path = args.get("path", "")
        if not isinstance(path, str):
            return f"Error: path must be a string, got {type(path).__name__}"
        if not path:
            return "Error: path is required"
        kb = get_kb_service()
        try:
            return kb.read_file(path)
        except PermissionError:
            return "Error: Invalid path"
        except FileNotFoundError:
            return "Error: File not found"
        except OSError as e:
            return f"Error: {e}"


class SearchKBTool(Tool):
    name = "search_kb"
    description = (
        "Search the knowledge base index by keyword. Returns matching entries with path, title, and summary. "
        "Use the returned path values with read_kb_file to read full content. "
        "If this returns no results or clearly off-topic results, fall back to search_kb_semantic."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query to match against titles, summaries, and tags",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (default 10, max 50)",
                "default": 10,
            },
        },
        "required": ["query"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        query = args.get("query", "")
        limit = min(args.get("limit", 10), 50)
        kb = get_kb_service()
        results = kb.search(query, limit=limit)
        return json.dumps([r.to_dict() for r in results], indent=2)


class SearchKBSemanticTool(Tool):
    name = "search_kb_semantic"
    description = (
        "Cloud path: LLM-powered semantic search of the knowledge base (older approach). "
        "Expands acronyms and synonyms via Azure OpenAI, then re-ranks file-level results. "
        "Prefer search_kb_hybrid for content questions — it is faster, chunk-level, and "
        "makes only one embedding call instead of two LLM calls. "
        "Use this only when search_kb_hybrid is unavailable or returns no relevant results."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 5, max 20)",
                "default": 5,
            },
        },
        "required": ["query"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        query = args.get("query", "").strip()
        limit = min(args.get("limit", 5), 20)

        if not query:
            return "Error: query is required"

        try:
            expanded_terms = self._expand_query(query)
        except Exception as e:
            logger.warning("Query expansion failed (%s), falling back to original query", e)
            expanded_terms = [query]

        kb = get_kb_service()
        seen_paths: set[str] = set()
        candidates = []
        for term in expanded_terms:
            for entry in kb.search(term, limit=15):
                if entry.path not in seen_paths:
                    seen_paths.add(entry.path)
                    candidates.append(entry)

        if not candidates:
            return json.dumps(
                {"results": [], "expanded_terms": expanded_terms, "note": "No matching KB documents found."},
                indent=2,
            )

        try:
            ranked = self._rerank(query, candidates, limit)
        except Exception as e:
            logger.warning("Re-ranking failed (%s), returning unranked candidates", e)
            ranked = [c.to_dict() for c in candidates[:limit]]

        return json.dumps(
            {"results": ranked, "expanded_terms": expanded_terms, "total_candidates": len(candidates)},
            indent=2,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_client(self):
        from app.config import get_settings
        s = get_settings()
        return AzureOpenAI(
            azure_endpoint=s.AZURE_OPENAI_ENDPOINT,
            api_key=s.AZURE_OPENAI_API_KEY,
            api_version=s.AZURE_OPENAI_API_VERSION,
        ), s.AZURE_OPENAI_DEPLOYMENT

    def _expand_query(self, query: str) -> list[str]:
        """Ask the LLM for 3-5 search terms that cover acronyms and synonyms."""
        client, deployment = self._get_client()
        resp = client.chat.completions.create(
            model=deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You expand Azure cloud search queries into multiple specific search terms. "
                        "Rules: expand acronyms (AKS→kubernetes, NSG→network security group, "
                        "KV→key vault, APIM→api management, AVD→azure virtual desktop, "
                        "RBAC→role assignment, AAD→entra identity, VNet→virtual network, "
                        "ACR→container registry, ASB→service bus, AFD→front door). "
                        "Include both the abbreviation and full form as separate entries. "
                        "Add related concepts. Return ONLY a JSON array of strings, no explanation."
                    ),
                },
                {"role": "user", "content": f"Query: {query}"},
            ],
            max_tokens=120,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip().strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        terms = json.loads(raw)
        if not isinstance(terms, list):
            return [query]
        return [str(t).strip() for t in terms if t][:6]

    def _rerank(self, query: str, candidates: list, limit: int) -> list[dict]:
        """Ask the LLM to pick the top-N most relevant documents from candidates."""
        if len(candidates) <= limit:
            return [c.to_dict() for c in candidates]

        client, deployment = self._get_client()
        # Cap the candidate list sent to the LLM to avoid large prompts
        pool = candidates[:30]
        doc_lines = "\n".join(
            f"{i + 1}. path={c.path} | title={c.title} | summary={c.summary[:100]}"
            for i, c in enumerate(pool)
        )
        resp = client.chat.completions.create(
            model=deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Rank Azure documentation by relevance to the user query. "
                        f"Return ONLY a JSON array of 1-based document indices, "
                        f"most relevant first, at most {limit} entries. No explanation."
                    ),
                },
                {"role": "user", "content": f"Query: {query}\n\nDocuments:\n{doc_lines}"},
            ],
            max_tokens=80,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip().strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        indices = json.loads(raw)

        result: list[dict] = []
        seen: set[str] = set()
        for idx in indices:
            i = int(idx) - 1
            if 0 <= i < len(pool) and pool[i].path not in seen:
                seen.add(pool[i].path)
                result.append(pool[i].to_dict())

        # Fill remaining slots from original candidate order if LLM returned fewer
        for c in candidates:
            if len(result) >= limit:
                break
            if c.path not in seen:
                seen.add(c.path)
                result.append(c.to_dict())

        return result[:limit]


def _keyword_hit(entry) -> dict:
    """Normalise a KBEntry (keyword fallback) to the search_kb_hybrid result schema.

    Keeps the field names consistent regardless of whether the hybrid index is
    warm or the tool fell back to keyword search — the agent should not need to
    branch on result shape.
    """
    return {
        "kb_path": entry.path,
        "heading": "",
        "snippet": (entry.summary or "")[:400],
        "source_url": None,
        "score": 0.0,
    }


class SearchKBHybridTool(Tool):
    name = "search_kb_hybrid"
    description = (
        "Local hybrid semantic + keyword search over chunked KB content. "
        "Returns the most relevant chunks (not full files) with kb_path, heading, "
        "snippet, and source_url. Prefer this over search_kb and search_kb_semantic "
        "for content questions — it is faster, runs entirely on-device (no extra cloud "
        "calls), and returns precise chunk-level results with citation URLs. "
        "Use read_kb_file only when you need the full file beyond the snippet."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language search query",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 5, max 20)",
                "default": 5,
            },
        },
        "required": ["query"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        from app.db.sqlite_vec_loader import hybrid_disabled, disabled_reason

        if hybrid_disabled():
            return json.dumps({
                "error": "search_kb_hybrid is unavailable on this deployment",
                "reason": disabled_reason(),
                "fallback": "Use search_kb or search_kb_semantic instead.",
            })

        query = args.get("query", "").strip()
        if not query:
            return "Error: query is required"

        limit = min(int(args.get("limit", 5)), 20)

        # Import here so startup is unaffected if embedder config is missing
        from app.kb.embedder import embed_query_for_search
        from app.kb.vector_store import chunk_count, hybrid_search, diversify_by_file
        from app.kb.reindex import status as reindex_status
        from app.kb.reranker import rerank_hits
        from app.db.engine import get_engine
        from app.config import get_settings

        engine = get_engine()
        with engine.connect() as conn:
            total_chunks = chunk_count(conn)

            if total_chunks == 0:
                # Index is empty — fall back to keyword search and note it
                kb = get_kb_service()
                fallback = kb.search(query, limit=limit)
                return json.dumps({
                    "results": [_keyword_hit(r) for r in fallback],
                    "note": "Hybrid index is empty or still building. Showing keyword fallback results.",
                    "index_state": reindex_status().get("state", "unknown"),
                })

            try:
                query_vec = embed_query_for_search(query)
            except Exception as e:
                logger.warning("Embedding failed for hybrid search (%s), falling back to keyword", e)
                kb = get_kb_service()
                fallback = kb.search(query, limit=limit)
                return json.dumps({
                    "results": [_keyword_hit(r) for r in fallback],
                    "note": f"Embedding unavailable ({e}). Showing keyword fallback results.",
                })

            # Fetch enough candidates to give the reranker some room to reorder.
            settings = get_settings()
            fetch_limit = max(limit, settings.KB_RERANK_TOP_K) if settings.KB_RERANK_ENABLED else limit
            hits = hybrid_search(conn, query, query_vec, limit=fetch_limit)

        # Re-rank with an LLM judge so the confidence signal comes from a
        # calibrated relevance score rather than corpus-specific vector
        # geometry. Falls back silently to RRF order on any error.
        hits = rerank_hits(query, hits)

        # Cap chunks-per-file so cross-cutting queries don't return three
        # chunks from one big doc when two other docs are also relevant.
        # Preserves the rerank ordering — best chunk per file still wins.
        hits = diversify_by_file(hits, limit=limit, max_per_file=settings.KB_DIVERSITY_MAX_PER_FILE)

        rs = reindex_status()
        results = [
            {
                "kb_path": h.kb_path,
                "heading": h.heading,
                "snippet": h.snippet,
                "source_url": h.source_url,
                "score": round(h.score, 4),
                "rerank_score": round(h.rerank_score, 3) if h.rerank_score is not None else None,
                "confidence": h.confidence,
            }
            for h in hits
        ]

        envelope: dict = {"results": results}

        # If no hit clears the medium-confidence bar, signal that to the agent
        # so it can say "I don't see a documented answer" instead of returning
        # what is effectively a random nearest-neighbour chunk.
        if hits and all(h.confidence == "low" for h in hits):
            envelope["low_confidence_only"] = True
            envelope["note"] = (
                "No high- or medium-confidence matches. The KB likely does not "
                "cover this query. Returned chunks are the nearest neighbours "
                "but may be irrelevant — treat with caution."
            )

        # Surface a warming note only while indexing is in progress
        if rs.get("state") == "running":
            indexed = rs.get("indexed_files", 0)
            total = rs.get("total_files", 0)
            envelope["note"] = (
                f"Index warming ({indexed}/{total} files). Results may be incomplete."
            )

        return json.dumps(envelope, indent=2)
