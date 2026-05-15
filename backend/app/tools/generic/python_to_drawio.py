"""
`generate_drawio_from_python` — write a `.drawio` file by describing the
diagram in mingrammer/diagrams Python.

Same DSL as `generate_python_diagram`, different backend. Instead of rendering
the script's output PNG via Graphviz, we capture the DOT source mid-flight,
run `dot -Tjson` to extract coordinates, then emit `.drawio` XML mapping
each mingrammer node to its Azure2 icon (where one exists) or a labelled
rectangle (where it doesn't). The resulting file opens in diagrams.net and
goes through the existing `validate_drawio` + auto-render pipeline so the
user can post-edit if they want.

The Python the LLM writes is identical to what `generate_python_diagram`
accepts, plus a header-injected `AzureGeneric(label, azure_icon=...)`
helper for services that don't have a mingrammer class.
"""

from __future__ import annotations

import ast
import logging
import os
import re
import sys
from pathlib import Path

from app.auth.models import User
from app.tools.base import SUBPROCESS_FLAGS, Tool
from app.tools.generic._drawio_emitter import pipeline as _pipeline
from app.tools.generic.python_diagram import _subprocess_env  # reuse the Windows-PATH fixup

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path("output")
_RENDER_TIMEOUT_S = 60

# Names made available to the user's code via the auto-injected capture
# header (see _drawio_emitter._CAPTURE_HEADER_TEMPLATE). These aren't
# explicit imports, so the AST allowlist below doesn't need to mention them.
_INJECTED_NAMES = {"AzureGeneric"}

# Same allowlist as `generate_python_diagram`. The user's code only imports
# from `diagrams.*`; the AzureGeneric helper is injected via header, not
# imported.
_ALLOWED_IMPORT_ROOTS = {"diagrams"}
_FORBIDDEN_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__",
    "input", "breakpoint", "globals", "locals", "vars",
    "getattr", "setattr", "delattr",
})
_FORBIDDEN_DUNDERS = frozenset({
    "__builtins__", "__class__", "__subclasses__", "__bases__",
    "__mro__", "__getattribute__", "__dict__", "__globals__",
    "__code__", "__init_subclass__", "__import__",
})

_FILENAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_ast(tree: ast.AST) -> str | None:
    has_diagram_block = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                root = n.name.split(".")[0]
                if root not in _ALLOWED_IMPORT_ROOTS:
                    return f"forbidden import: '{n.name}'. Only `diagrams.*` imports are allowed."
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                return "relative imports are not allowed"
            root = node.module.split(".")[0]
            if root not in _ALLOWED_IMPORT_ROOTS:
                return f"forbidden import: 'from {node.module}'. Only `from diagrams...` imports are allowed."
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            return f"forbidden builtin: '{node.id}'"
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_DUNDERS:
            return f"forbidden attribute access: '{node.attr}'"
        elif isinstance(node, ast.With):
            for item in node.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Name) and ctx.func.id == "Diagram":
                    has_diagram_block = True

    if not has_diagram_block:
        return "code must contain a `with Diagram(...)`: block."
    return None


class _DiagramKwargInjector(ast.NodeTransformer):
    """Force show=False on every Diagram() call so the script doesn't pop a
    viewer or rely on the filename arg. The drawio emitter ignores
    outformat/filename — only `name` (the title) and the body matter."""

    def __init__(self) -> None:
        self.found = 0
        self.title: str | None = None

    def visit_With(self, node: ast.With) -> ast.With:
        self.generic_visit(node)
        for item in node.items:
            ctx = item.context_expr
            if not (isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Name) and ctx.func.id == "Diagram"):
                continue
            self.found += 1
            # Strip show / filename / outformat — we control rendering.
            ctx.keywords = [kw for kw in ctx.keywords if kw.arg not in {"show", "filename", "outformat"}]
            ctx.keywords.append(ast.keyword(arg="show", value=ast.Constant(False)))
            # Capture the title from the first positional arg, if any.
            if self.title is None and ctx.args:
                first = ctx.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    self.title = first.value
        return node


class GenerateDrawioFromPythonTool(Tool):
    name = "generate_drawio_from_python"
    description = (
        "Generate an editable `.drawio` architecture diagram by describing it "
        "in mingrammer `diagrams` Python — same DSL as `generate_python_diagram`. "
        "Instead of producing a PNG via Graphviz, this tool captures the layout, "
        "maps each mingrammer node to its Azure2 SVG icon (where available), and "
        "emits a `.drawio` XML file. The result opens in diagrams.net for "
        "post-editing, auto-runs the drawio validator, and auto-renders to PNG. "
        "\n\n"
        "Use this when the user wants a draw.io file they can hand-edit later, "
        "or when Microsoft-reference visual styling matters. For one-shot PNGs "
        "where draw.io isn't needed, use `generate_python_diagram` instead.\n\n"
        "The script may use `AzureGeneric(\"Label\", azure_icon=\"bastions\")` "
        "for services mingrammer doesn't ship — it renders as a plain box in "
        "intermediate Graphviz layout, and gets upgraded to the matching Azure2 "
        "SVG in the final drawio output. Available azure_icon values include: "
        "bastion, waf_policy, entra_id, private_link, vnet_peering, openai, policy."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": (
                    "Output filename stem (no extension). Letters, digits, "
                    "underscore, hyphen only. Produces output/<filename>.drawio "
                    "(plus auto-rendered .png and the captured .py)."
                ),
            },
            "code": {
                "type": "string",
                "description": (
                    "Full Python script. Must include `from diagrams import ...` "
                    "and a `with Diagram('Title', ...):` block. Use the same "
                    "syntax as `generate_python_diagram`. AzureGeneric is "
                    "available without an import."
                ),
            },
            "title": {
                "type": "string",
                "description": (
                    "Optional title for the drawio file's title block. If "
                    "omitted, the first positional arg to Diagram() is used."
                ),
            },
        },
        "required": ["filename", "code"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        if not isinstance(args, dict):
            return "Error: invalid arguments - expected an object with filename and code"

        filename = (args.get("filename") or args.get("name") or "").strip()
        code = args.get("code") or args.get("script") or args.get("content") or ""
        explicit_title = args.get("title") or ""

        if not filename:
            return "Error: filename is required"
        stem = Path(filename).stem
        if not _FILENAME_RE.match(stem):
            return (
                "Error: filename may only contain letters, digits, underscore, "
                "and hyphen."
            )
        if not code.strip():
            return "Error: code is required"

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return (
                f"Error: Python syntax error on line {e.lineno}: {e.msg}. "
                f"Fix the syntax and retry."
            )

        err = _validate_ast(tree)
        if err is not None:
            return f"Error: code rejected by safety validator - {err}"

        injector = _DiagramKwargInjector()
        modified_tree = injector.visit(tree)
        ast.fix_missing_locations(modified_tree)
        if injector.found == 0:
            return (
                "Error: no `with Diagram(...)` call found. Wrap your nodes and "
                "edges in a Diagram context manager."
            )

        title = explicit_title or injector.title or stem.replace("_", " ").replace("-", " ").title()
        prepared_code = ast.unparse(modified_tree)

        sandbox = _OUTPUT_DIR.resolve()
        sandbox.mkdir(parents=True, exist_ok=True)
        drawio_path = sandbox / f"{stem}.drawio"

        try:
            xml, py_source = _pipeline(
                user_code=prepared_code,
                sandbox=sandbox,
                stem=stem,
                title=title,
                env=_subprocess_env(),
                python_exe=sys.executable,
                timeout=_RENDER_TIMEOUT_S,
                subprocess_flags=SUBPROCESS_FLAGS,
            )
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:  # noqa: BLE001
            logger.exception("python-to-drawio pipeline crashed")
            return f"Error: pipeline crashed - {e}"

        try:
            drawio_path.write_text(xml, encoding="utf-8")
        except OSError as e:
            return f"Error writing .drawio file: {e}"

        size_kb = drawio_path.stat().st_size // 1024
        logger.info(
            "Generated drawio from python: %s.drawio (%d KB) for %s",
            stem, size_kb, user.email,
        )

        # Auto-validate the resulting .drawio (same as generate_file does).
        try:
            from app.tools.generic.validate_drawio import validate_drawio_file
            report = validate_drawio_file(drawio_path)
        except Exception as e:  # noqa: BLE001
            report = f"Validation skipped due to error: {e}"

        # Auto-render to PNG so the agent gets a vision-attached image.
        render_note = ""
        if "Validation FAILED: XML parse error" not in report:
            try:
                from app.tools.generic.render_drawio import render_drawio_to_disk
                out_path, mode, render_err = render_drawio_to_disk(f"{stem}.drawio", "png")
            except Exception as e:  # noqa: BLE001
                out_path, mode, render_err = None, None, str(e)
            if out_path is not None:
                kb = out_path.stat().st_size // 1024
                render_note = (
                    f"\n\nAuto-render: output/{out_path.name} ({kb} KB, via {mode}). "
                    "The image is being attached to your next turn for visual review."
                )
            elif render_err and "not installed" not in render_err.lower():
                render_note = f"\n\nAuto-render skipped: {render_err}"

        return (
            f"Diagram written: output/{stem}.drawio ({size_kb} KB)\n"
            f"Captured Python source: output/{stem}.py\n\n"
            f"Auto-validation:\n{report}{render_note}"
        )
