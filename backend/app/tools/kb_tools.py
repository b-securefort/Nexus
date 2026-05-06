"""
KB tools — read_kb_file, search_kb, search_kb_semantic.
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
    description = "Read the contents of a file from the knowledge base. Path should be relative to the KB root, e.g. 'kb/adrs/adr-001.md'."
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
        path = args.get("path", "")
        kb = get_kb_service()
        try:
            return kb.read_file(path)
        except PermissionError:
            return "Error: Invalid path"
        except FileNotFoundError:
            return "Error: File not found"


class SearchKBTool(Tool):
    name = "search_kb"
    description = "Search the knowledge base index by keyword. Returns matching entries with path, title, and summary."
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
        "LLM-powered semantic search of the knowledge base. "
        "Expands acronyms and synonyms before searching "
        "(e.g. 'AKS' → 'kubernetes', 'NSG' → 'network security group', 'KV' → 'key vault'), "
        "then re-ranks candidates by relevance to the original question. "
        "Use this when search_kb returns no results or clearly off-topic results."
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
