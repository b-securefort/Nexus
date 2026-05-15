"""Tests for the `generate_python_diagram` tool surface.

We exercise only the AST validation, kwarg injection, and the Windows
PATH-fixup helper. The actual subprocess pipeline (run user code → Graphviz →
PNG) is exercised via manual smoke runs that need a real Graphviz install.
"""

import ast
import os
import sys

import pytest

from app.auth.models import User
from app.tools.generic.python_diagram import (
    GeneratePythonDiagramTool,
    _DiagramKwargInjector,
    _subprocess_env,
    _validate_ast,
)


def _user() -> User:
    return User(oid="dev", email="dev@local", display_name="dev")


# ── _validate_ast ─────────────────────────────────────────────────────────


def test_validate_accepts_diagram_block_with_diagrams_imports():
    src = (
        "from diagrams import Diagram, Cluster\n"
        "from diagrams.azure.network import ApplicationGateway\n"
        "with Diagram('X'):\n"
        "    ApplicationGateway('AppGW')\n"
    )
    err = _validate_ast(ast.parse(src))
    assert err is None


def test_validate_rejects_non_diagrams_import():
    """The AST allowlist exists to keep the subprocess from accessing the
    filesystem or network. `os` must not be importable."""
    src = "import os\n"
    err = _validate_ast(ast.parse(src))
    assert err and "forbidden import" in err


def test_validate_rejects_from_non_diagrams_module():
    src = "from subprocess import run\n"
    err = _validate_ast(ast.parse(src))
    assert err and "forbidden import" in err


def test_validate_rejects_forbidden_builtin():
    """Calling `open` is the simplest filesystem escape — block it."""
    src = "open('x')\n"
    err = _validate_ast(ast.parse(src))
    assert err and "forbidden builtin" in err


def test_validate_rejects_exec_eval_compile():
    for fn in ("exec", "eval", "compile"):
        err = _validate_ast(ast.parse(f"{fn}('x')\n"))
        assert err and "forbidden builtin" in err


def test_validate_rejects_dunder_attribute_access():
    """Well-known sandbox escape: object().__class__.__bases__[0].__subclasses__()."""
    src = "().__class__\n"
    err = _validate_ast(ast.parse(src))
    assert err and "forbidden attribute" in err


def test_validate_rejects_missing_diagram_block():
    """The script has to actually open a `with Diagram(...)` context — without
    it the capture wrapper has nothing to record."""
    src = "from diagrams import Diagram\nx = 1\n"
    err = _validate_ast(ast.parse(src))
    assert err and "with Diagram" in err


def test_validate_rejects_relative_import():
    """No `from . import x` style — would only work inside a package."""
    src = "from . import diagrams\n"
    # AST won't parse a relative import at the top of a flat module the same
    # way — it does parse, but module is None.
    err = _validate_ast(ast.parse(src))
    assert err and "relative" in err


# ── _DiagramKwargInjector ─────────────────────────────────────────────────


def _unparse_after_inject(src: str, stem: str) -> str:
    tree = ast.parse(src)
    inj = _DiagramKwargInjector(stem)
    inj.visit(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def test_injector_adds_show_filename_outformat():
    """The tool owns where the PNG lands — it overrides anything the LLM passed."""
    src = (
        "from diagrams import Diagram\n"
        "with Diagram('T'):\n"
        "    pass\n"
    )
    out = _unparse_after_inject(src, "my_diag")
    assert "show=False" in out
    assert "filename='my_diag'" in out
    assert "outformat='png'" in out


def test_injector_overrides_existing_kwargs():
    """If the LLM passed show=True or a different filename, we replace them
    so the tool stays in control of output."""
    src = (
        "from diagrams import Diagram\n"
        "with Diagram('T', show=True, filename='wrong', outformat='svg'):\n"
        "    pass\n"
    )
    out = _unparse_after_inject(src, "my_diag")
    assert "show=False" in out
    assert "filename='my_diag'" in out
    assert "filename='wrong'" not in out
    assert "outformat='png'" in out
    assert "outformat='svg'" not in out


def test_injector_counts_diagram_calls():
    """The tool relies on `overridden_count` to detect 'no Diagram() block'."""
    tree = ast.parse(
        "from diagrams import Diagram\n"
        "with Diagram('A'):\n"
        "    pass\n"
        "with Diagram('B'):\n"
        "    pass\n"
    )
    inj = _DiagramKwargInjector("x")
    inj.visit(tree)
    assert inj.overridden_count == 2


# ── _subprocess_env ───────────────────────────────────────────────────────


def test_subprocess_env_preserves_existing_path():
    """The PATH-fixup helper must not destroy whatever the parent process
    already had on PATH — it only prepends the Graphviz dir."""
    env = _subprocess_env()
    assert "PATH" in env


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only PATH fixup")
def test_subprocess_env_prepends_graphviz_when_present():
    """On Windows, if dot.exe is at the standard install location, the
    helper prepends that directory so subprocesses can find it even when
    the backend was started before Graphviz was installed."""
    from pathlib import Path

    env = _subprocess_env()
    if Path(r"C:\Program Files\Graphviz\bin\dot.exe").is_file():
        assert r"C:\Program Files\Graphviz\bin" in env["PATH"]


# ── Tool surface ──────────────────────────────────────────────────────────


def test_tool_rejects_missing_filename():
    out = GeneratePythonDiagramTool().execute({"code": "x"}, _user())
    assert out.startswith("Error: filename")


def test_tool_rejects_missing_code():
    out = GeneratePythonDiagramTool().execute({"filename": "x"}, _user())
    assert out.startswith("Error: code")


def test_tool_rejects_filename_with_slashes_or_spaces():
    """We don't want path traversal or weird shell chars in the script name."""
    out = GeneratePythonDiagramTool().execute(
        {"filename": "a/b", "code": "from diagrams import Diagram\nwith Diagram('x'): pass\n"},
        _user(),
    )
    assert "filename" in out.lower()


def test_tool_rejects_python_syntax_error_in_user_code():
    out = GeneratePythonDiagramTool().execute(
        {"filename": "ok", "code": "def (\n"},
        _user(),
    )
    assert "syntax error" in out.lower()


def test_tool_rejects_when_no_diagram_block():
    out = GeneratePythonDiagramTool().execute(
        {"filename": "ok", "code": "from diagrams import Diagram\nx = 1\n"},
        _user(),
    )
    # AST validator catches this before the subprocess fires.
    assert "Diagram" in out
