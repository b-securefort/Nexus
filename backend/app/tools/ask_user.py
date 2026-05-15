"""
`ask_user` — present multiple-choice clarifying questions to the user before
proceeding. Skill-agnostic: any skill that benefits from clarification adds
this tool to its `tools:` list.

The tool itself is a thin validation+error layer. The orchestrator special-
cases it: when the model emits an ask_user call, the orchestrator persists a
PendingQuestion, emits a `question_required` SSE event, awaits the user's
answer, and feeds the answers back to the model as the tool result. This file
defines validation only; orchestrator integration lives in orchestrator.py.
"""

import json
import logging

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)


_MAX_QUESTIONS = 4
_MIN_OPTIONS = 2
_MAX_OPTIONS = 4
# Headers are short section titles rendered on their own row above each
# question. A generous backstop keeps a confused model from pasting a
# paragraph here, but the limit is high enough that natural phrases never
# trip it.
_MAX_HEADER_LEN = 80


def validate_questions(raw: object) -> tuple[list[dict] | None, str | None]:
    """Validate the agent's `questions` argument.

    Returns (validated_list, None) on success or (None, error_message) on
    failure. The validated list is what the orchestrator persists and what
    the frontend renders, so this function is the single source of truth
    for what a question payload looks like.
    """
    if not isinstance(raw, list):
        return None, "questions must be a list"
    if not (1 <= len(raw) <= _MAX_QUESTIONS):
        return None, f"questions must contain 1-{_MAX_QUESTIONS} entries (got {len(raw)})"

    cleaned: list[dict] = []
    for i, q in enumerate(raw):
        if not isinstance(q, dict):
            return None, f"questions[{i}] must be an object"
        question = (q.get("question") or "").strip()
        header = (q.get("header") or "").strip()
        options = q.get("options")
        multi_select = bool(q.get("multi_select", False))
        if not question:
            return None, f"questions[{i}].question is required"
        if not header:
            return None, f"questions[{i}].header is required"
        if len(header) > _MAX_HEADER_LEN:
            return None, (
                f"questions[{i}].header must be <= {_MAX_HEADER_LEN} chars "
                f"(got {len(header)})"
            )
        if not isinstance(options, list):
            return None, f"questions[{i}].options must be a list"
        if not (_MIN_OPTIONS <= len(options) <= _MAX_OPTIONS):
            return None, (
                f"questions[{i}].options must contain {_MIN_OPTIONS}-{_MAX_OPTIONS} "
                f"entries (got {len(options)})"
            )

        cleaned_options: list[dict] = []
        for j, opt in enumerate(options):
            if not isinstance(opt, dict):
                return None, f"questions[{i}].options[{j}] must be an object"
            label = (opt.get("label") or "").strip()
            description = (opt.get("description") or "").strip()
            if not label:
                return None, f"questions[{i}].options[{j}].label is required"
            cleaned_options.append({"label": label, "description": description})

        cleaned.append({
            "question": question,
            "header": header,
            "options": cleaned_options,
            "multi_select": multi_select,
        })
    return cleaned, None


class AskUserTool(Tool):
    name = "ask_user"
    description = (
        "Pose 1-4 multiple-choice clarifying questions to the user before doing "
        "work that depends on ambiguous requirements. Each question has 2-4 "
        "labelled options; the user can also pick 'Other' and supply free text. "
        "Use this when a request leaves topology, scope, target environment, or "
        "an optional component unspecified - asking up-front is cheaper than "
        "guessing and reworking. Don't use it for trivial preferences or things "
        "you can infer from context. The result is JSON: a list of objects with "
        "{question, selected:[label,...], notes?} so multi-select and 'Other' "
        "free text are both represented."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": _MAX_QUESTIONS,
                "description": (
                    "1-4 questions to ask in a single batch. Group them so the "
                    "user answers everything in one round-trip."
                ),
                "items": {
                    "type": "object",
                    "required": ["question", "header", "options"],
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Full sentence ending with a question mark.",
                        },
                        "header": {
                            "type": "string",
                            "description": (
                                f"Section title rendered on its own row above "
                                f"the question. Should be a natural phrase like "
                                f"'Topology', 'Backend access pattern', "
                                f"'Monitoring and identity'. Max "
                                f"{_MAX_HEADER_LEN} chars."
                            ),
                        },
                        "options": {
                            "type": "array",
                            "minItems": _MIN_OPTIONS,
                            "maxItems": _MAX_OPTIONS,
                            "items": {
                                "type": "object",
                                "required": ["label"],
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "description": "Short option text (1-5 words).",
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": (
                                            "Optional one-line elaboration shown "
                                            "under the label. Helpful when an option "
                                            "has a non-obvious tradeoff."
                                        ),
                                    },
                                },
                            },
                        },
                        "multi_select": {
                            "type": "boolean",
                            "description": (
                                "If true, the user can pick more than one option. "
                                "Default: false (radio button)."
                            ),
                        },
                    },
                },
            },
        },
        "required": ["questions"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        # The orchestrator handles ask_user specially - it validates the args,
        # creates a PendingQuestion, emits a question_required SSE event, and
        # blocks on the user's answer before continuing. If execute() is ever
        # invoked directly (e.g. by a test or a non-streaming call site), we
        # return a structured error so the failure mode is loud, not silent.
        validated, err = validate_questions(args.get("questions"))
        if err is not None:
            return f"Error: {err}"
        return json.dumps({
            "status": "error",
            "message": (
                "ask_user must be invoked through the streaming orchestrator so "
                "the user has a chance to answer. Direct execution is not "
                "supported."
            ),
            "questions": validated,
        })
