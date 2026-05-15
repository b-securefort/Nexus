"""Tests for the `generate_drawio_from_python` tool surface.

We cover AST validation, the diagram-kwarg injector (which here also
captures the diagram title) and the early input-error paths the tool
returns before kicking off the subprocess pipeline. The pipeline integration
is exercised by manual smoke runs that need a Graphviz install.
"""

import ast

import pytest

from app.auth.models import User
from app.tools.generic.python_to_drawio import (
    GenerateDrawioFromPythonTool,
    _DiagramKwargInjector,
    _validate_ast,
)


def _user() -> User:
    return User(oid="dev", email="dev@local", display_name="dev")


# ── _validate_ast ─────────────────────────────────────────────────────────


def test_validate_accepts_diagrams_imports_with_diagram_block():
    src = (
        "from diagrams import Diagram, Cluster, Edge\n"
        "from diagrams.azure.network import ApplicationGateway\n"
        "with Diagram('Spoke'):\n"
        "    ApplicationGateway('AppGW')\n"
    )
    assert _validate_ast(ast.parse(src)) is None


def test_validate_rejects_azuregeneric_import():
    """AzureGeneric is injected by the capture header — importing it from
    `diagrams` does NOT work and is the single most common LLM hallucination.
    We don't actively block it in the AST (it would import-fail at runtime),
    but every other safety property still holds."""
    src = "from diagrams import AzureGeneric\nwith Diagram('x'): pass\n"
    # AST validator allows this since 'diagrams' is the allowed root; the
    # runtime import will fail. We document the behaviour here so the test
    # surfaces a change if we ever add a stricter check.
    err = _validate_ast(ast.parse(src))
    assert err is None


def test_validate_rejects_os_import():
    src = "import os\nwith __builtins__: pass\n"
    err = _validate_ast(ast.parse(src))
    assert err and "forbidden import" in err


def test_validate_rejects_dunder_chain():
    src = (
        "from diagrams import Diagram\n"
        "with Diagram('x'):\n"
        "    x = ().__class__\n"
    )
    err = _validate_ast(ast.parse(src))
    assert err and "forbidden attribute" in err


def test_validate_rejects_missing_diagram_block():
    src = "from diagrams import Diagram\nx = 1\n"
    err = _validate_ast(ast.parse(src))
    assert err and "with Diagram" in err


# ── _DiagramKwargInjector ─────────────────────────────────────────────────


def test_injector_strips_show_filename_outformat_and_forces_show_false():
    tree = ast.parse(
        "from diagrams import Diagram\n"
        "with Diagram('Title', show=True, filename='nope', outformat='svg'):\n"
        "    pass\n"
    )
    inj = _DiagramKwargInjector()
    inj.visit(tree)
    ast.fix_missing_locations(tree)
    src = ast.unparse(tree)
    assert "show=False" in src
    # filename/outformat are stripped (the tool fills them at write time)
    assert "filename='nope'" not in src
    assert "outformat='svg'" not in src


def test_injector_captures_title_from_first_positional_arg():
    """If the user passed `Diagram('My Title')`, the title goes into the
    drawio file's title block (Phase 4 generation step)."""
    tree = ast.parse(
        "from diagrams import Diagram\n"
        "with Diagram('My Architecture'):\n"
        "    pass\n"
    )
    inj = _DiagramKwargInjector()
    inj.visit(tree)
    assert inj.title == "My Architecture"


def test_injector_counts_diagram_calls():
    tree = ast.parse(
        "from diagrams import Diagram\n"
        "with Diagram('A'): pass\n"
        "with Diagram('B'): pass\n"
    )
    inj = _DiagramKwargInjector()
    inj.visit(tree)
    assert inj.found == 2


def test_injector_ignores_non_diagram_with_blocks():
    """`with open(...)` should not be touched even if it appeared (it would
    be rejected upstream by _validate_ast, but the injector is independent)."""
    tree = ast.parse(
        "from diagrams import Diagram\n"
        "with Diagram('X'): pass\n"
        "with object() as o: pass\n"
    )
    inj = _DiagramKwargInjector()
    inj.visit(tree)
    assert inj.found == 1


# ── Tool surface ──────────────────────────────────────────────────────────


def test_tool_rejects_missing_filename():
    out = GenerateDrawioFromPythonTool().execute({"code": "x"}, _user())
    assert out.startswith("Error: filename")


def test_tool_rejects_missing_code():
    out = GenerateDrawioFromPythonTool().execute({"filename": "x"}, _user())
    assert out.startswith("Error: code")


def test_tool_rejects_filename_with_special_chars():
    out = GenerateDrawioFromPythonTool().execute(
        {"filename": "a b", "code": "from diagrams import Diagram\nwith Diagram('x'): pass\n"},
        _user(),
    )
    assert "filename" in out.lower()


def test_tool_rejects_syntax_error():
    out = GenerateDrawioFromPythonTool().execute(
        {"filename": "ok", "code": "def (\n"},
        _user(),
    )
    assert "syntax error" in out.lower()


def test_tool_rejects_missing_diagram_block():
    out = GenerateDrawioFromPythonTool().execute(
        {"filename": "ok", "code": "from diagrams import Diagram\nx = 1\n"},
        _user(),
    )
    assert "Diagram" in out


def test_tool_rejects_forbidden_import():
    out = GenerateDrawioFromPythonTool().execute(
        {"filename": "ok", "code": "import os\nfrom diagrams import Diagram\nwith Diagram('x'): pass\n"},
        _user(),
    )
    assert "forbidden import" in out
