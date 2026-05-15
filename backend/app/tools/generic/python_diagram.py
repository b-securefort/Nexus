"""
Generate architecture diagrams from Python code using the `diagrams` library.

This tool exists as an alternative to the .drawio toolchain. Where draw.io
needs the LLM to pick absolute pixel coordinates per cell, the `diagrams`
library uses Graphviz to lay out automatically — the LLM only declares the
graph shape (containers, nodes, edges) and gets a rendered PNG back.

Safety: the code is parsed via `ast`; only `from diagrams[...]` imports are
permitted, a dangerous-builtin denylist is applied, and the script is run in
a subprocess with a timeout. The output is sandboxed to output/.
"""

import ast
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from app.auth.models import User
from app.tools.base import SUBPROCESS_FLAGS, Tool

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path("output")
_RENDER_TIMEOUT_S = 60

# Standard locations Graphviz `dot` lands on Windows when installed via the
# winget package or the official MSI. The Python `diagrams` library shells
# out to `dot`, so we make sure the subprocess can find it even if the
# backend was started before Graphviz was added to PATH.
_WIN_GRAPHVIZ_DIRS = (
    r"C:\Program Files\Graphviz\bin",
    r"C:\Program Files (x86)\Graphviz\bin",
)


def _subprocess_env() -> dict[str, str]:
    """Copy of os.environ with the Graphviz bin dir prepended to PATH on
    Windows if `dot` isn't already discoverable. This makes the tool robust
    against a stale parent PATH (e.g. the backend was started before
    Graphviz was installed)."""
    env = os.environ.copy()
    if sys.platform != "win32":
        return env
    path = env.get("PATH", "")
    for d in _WIN_GRAPHVIZ_DIRS:
        if Path(d, "dot.exe").is_file() and d.lower() not in path.lower():
            env["PATH"] = d + os.pathsep + path
            break
    return env

# Imports allowed by AST validation. Only the `diagrams` package and its
# nested modules — nothing that touches the filesystem, network, or OS.
_ALLOWED_IMPORT_ROOTS = {"diagrams"}

# Built-in names that enable arbitrary code execution or filesystem access.
# Blocking by name is shallow, but combined with the import allowlist it
# removes the obvious escape hatches an LLM might use.
_FORBIDDEN_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__",
    "input", "breakpoint", "globals", "locals", "vars",
    "getattr", "setattr", "delattr",
})

# Dunder attribute access used in well-known sandbox escapes
# (e.g. ().__class__.__bases__[0].__subclasses__()).
_FORBIDDEN_DUNDERS = frozenset({
    "__builtins__", "__class__", "__subclasses__", "__bases__",
    "__mro__", "__getattribute__", "__dict__", "__globals__",
    "__code__", "__init_subclass__", "__import__",
})

_FILENAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_ast(tree: ast.AST) -> str | None:
    """Walk the AST and reject anything outside the diagrams allowlist.
    Returns an error string, or None if the script is safe to run.
    """
    has_diagram_block = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                root = n.name.split(".")[0]
                if root not in _ALLOWED_IMPORT_ROOTS:
                    return (
                        f"forbidden import: '{n.name}'. Only `diagrams.*` "
                        "imports are allowed in this tool."
                    )
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                return "relative imports are not allowed"
            root = node.module.split(".")[0]
            if root not in _ALLOWED_IMPORT_ROOTS:
                return (
                    f"forbidden import: 'from {node.module}'. Only "
                    "`from diagrams...` imports are allowed."
                )
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
        return (
            "code must contain a `with Diagram(...):` block. "
            "Wrap the body of your diagram in one."
        )
    return None


class _DiagramKwargInjector(ast.NodeTransformer):
    """Force `show=False`, `filename=<stem>`, `outformat="png"` on every
    `with Diagram(...)` call. Whatever the LLM passed for those kwargs is
    replaced, so the tool controls where the PNG lands.
    """

    def __init__(self, stem: str) -> None:
        self.stem = stem
        self.overridden_count = 0

    def visit_With(self, node: ast.With) -> ast.With:
        self.generic_visit(node)
        for item in node.items:
            ctx = item.context_expr
            if not (isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Name) and ctx.func.id == "Diagram"):
                continue
            ctx.keywords = [kw for kw in ctx.keywords if kw.arg not in {"show", "filename", "outformat"}]
            ctx.keywords.extend([
                ast.keyword(arg="show", value=ast.Constant(False)),
                ast.keyword(arg="filename", value=ast.Constant(self.stem)),
                ast.keyword(arg="outformat", value=ast.Constant("png")),
            ])
            self.overridden_count += 1
        return node


def _looks_like_graphviz_missing(stderr: str) -> bool:
    s = stderr.lower()
    return (
        "failed to execute" in s and "dot" in s
        or "executablenotfound" in s
        or "graphviz" in s and ("not found" in s or "no such file" in s)
    )


class GeneratePythonDiagramTool(Tool):
    name = "generate_python_diagram"
    description = (
        "Render an architecture diagram from Python code using the `diagrams` "
        "library (mingrammer/diagrams). Unlike generate_file with .drawio, this "
        "tool requires no manual coordinates: you declare containers (Cluster), "
        "nodes (Azure/AWS/GCP service icons), and edges, and Graphviz lays out "
        "the diagram automatically. The result is a PNG in output/. "
        "\n\n"
        "Use this for clean architecture diagrams when interactive draw.io editing "
        "is not required. Output looks more 'autogenerated' than draw.io but is "
        "almost always correct on the first try.\n\n"
        "Required code shape:\n"
        "  from diagrams import Diagram, Cluster, Edge\n"
        "  from diagrams.azure.network import ApplicationGateway, ...\n"
        "  with Diagram('Title'):\n"
        "      # nodes, clusters, edges here\n"
        "\n"
        "The tool overrides any `show=`, `filename=`, `outformat=` kwargs you "
        "pass on the Diagram() call — the filename you pass to the tool wins. "
        "Only imports from `diagrams.*` are allowed; other imports are rejected "
        "for safety."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": (
                    "Output filename stem (no extension). Letters, digits, "
                    "underscore, hyphen only. The tool writes both "
                    "output/<filename>.png and output/<filename>.py."
                ),
            },
            "code": {
                "type": "string",
                "description": (
                    "Full Python script. Must include `from diagrams import ...` "
                    "and a `with Diagram(...):` block containing the diagram body."
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

        if not filename:
            return "Error: filename is required"
        stem = Path(filename).stem
        if not _FILENAME_RE.match(stem):
            return (
                "Error: filename may only contain letters, digits, underscore, "
                "and hyphen (no spaces, paths, or extensions other than .png)."
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

        injector = _DiagramKwargInjector(stem)
        modified_tree = injector.visit(tree)
        ast.fix_missing_locations(modified_tree)
        if injector.overridden_count == 0:
            return (
                "Error: no `with Diagram(...)` call found to inject output kwargs. "
                "Wrap your nodes/edges in a Diagram context manager."
            )

        final_code = ast.unparse(modified_tree)

        sandbox = _OUTPUT_DIR.resolve()
        sandbox.mkdir(parents=True, exist_ok=True)
        script_path = sandbox / f"{stem}.py"
        png_path = sandbox / f"{stem}.png"

        try:
            script_path.write_text(final_code, encoding="utf-8")
        except OSError as e:
            return f"Error writing script: {e}"

        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=str(sandbox),
                capture_output=True,
                text=True,
                timeout=_RENDER_TIMEOUT_S,
                env=_subprocess_env(),
                **SUBPROCESS_FLAGS,
            )
        except subprocess.TimeoutExpired:
            return f"Error: rendering timed out after {_RENDER_TIMEOUT_S}s. Simplify the diagram and retry."
        except OSError as e:
            return f"Error launching Python interpreter: {e}"

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if _looks_like_graphviz_missing(stderr):
                return (
                    "Error: Graphviz `dot` binary not found. The `diagrams` "
                    "library needs the system Graphviz install to render. "
                    "Install via:\n"
                    "  winget install Graphviz.Graphviz   (Windows)\n"
                    "  brew install graphviz              (macOS)\n"
                    "  apt-get install graphviz           (Debian/Ubuntu)\n"
                    "Then ensure `dot` is on PATH and retry."
                )
            return (
                f"Error: Python script failed (exit {result.returncode}).\n"
                f"stderr (last 1000 chars): {stderr[-1000:]}\n"
                f"Source kept at: output/{stem}.py"
            )

        if not png_path.exists():
            return (
                f"Error: rendering reported success but {stem}.png was not "
                f"produced. Source: output/{stem}.py"
            )

        size_kb = png_path.stat().st_size // 1024
        logger.info(
            "Rendered python diagram: %s.png (%d KB) for %s",
            stem, size_kb, user.email,
        )
        return (
            f"Diagram rendered: output/{stem}.png ({size_kb} KB)\n"
            f"Source: output/{stem}.py\n\n"
            "The rendered image is being attached to your next turn for visual "
            "review. Inspect it and check:\n"
            "  - Container nesting matches your architecture\n"
            "  - Edges connect the right nodes and have the right direction\n"
            "  - Cluster labels read correctly\n"
            "  - Any flow labels on edges (`Edge(label=...)`) are placed sensibly\n\n"
            "If something is wrong, edit the Python and re-run this tool with "
            "the same filename (it overwrites)."
        )
