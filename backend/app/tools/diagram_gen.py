"""
Mermaid diagram generation tool — generates architecture/flow diagrams as Mermaid syntax.
No external dependencies, no approval needed.
"""

import logging

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)


class DiagramGenTool(Tool):
    name = "diagram_gen"
    description = (
        "Generate architecture diagrams, flow charts, and sequence diagrams as Mermaid syntax. "
        "The output can be rendered in markdown or saved as a .md file via generate_file. "
        "Use this when the user asks for a visual diagram of their Azure architecture, "
        "deployment flow, network topology, or any system design."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "diagram_type": {
                "type": "string",
                "enum": ["flowchart", "sequence", "architecture", "class", "state", "er", "gantt"],
                "description": "Type of Mermaid diagram to generate.",
            },
            "description": {
                "type": "string",
                "description": (
                    "Natural language description of what the diagram should show. "
                    "Be specific about components, connections, and flow direction."
                ),
            },
            "mermaid_code": {
                "type": "string",
                "description": (
                    "The actual Mermaid diagram code. The LLM should generate valid Mermaid syntax. "
                    "If provided, this is returned directly (validated). "
                    "If omitted, the description is returned as a prompt for the LLM to generate the diagram."
                ),
            },
        },
        "required": ["diagram_type"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        diagram_type = args.get("diagram_type", "flowchart")
        description = args.get("description", "")
        mermaid_code = args.get("mermaid_code", "")

        if mermaid_code:
            # Validate it starts with a valid Mermaid directive
            first_line = mermaid_code.strip().split("\n")[0].strip().lower()
            valid_starts = [
                "flowchart", "graph", "sequencediagram", "sequence",
                "classDiagram", "class", "statediagram", "state",
                "erdiagram", "er", "gantt", "pie", "mindmap",
                "%%{", "---",
            ]
            # Mermaid is flexible, just return it wrapped
            return (
                f"```mermaid\n{mermaid_code.strip()}\n```\n\n"
                f"Diagram type: {diagram_type}\n"
                f"Description: {description or 'N/A'}"
            )

        if description:
            return (
                f"Diagram request received.\n"
                f"Type: {diagram_type}\n"
                f"Description: {description}\n\n"
                f"Please generate the Mermaid code and call diagram_gen again with the mermaid_code parameter, "
                f"or call generate_file to save it as a .md file."
            )

        return "Error: Either description or mermaid_code is required."
