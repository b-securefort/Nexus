"""
KB tools — read_kb_file and search_kb.
"""

import json

from app.auth.models import User
from app.kb.service import get_kb_service
from app.tools.base import Tool


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
