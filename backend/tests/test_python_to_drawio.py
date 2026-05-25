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
    _DIAGRAM_IMPORTS,
    _DiagramKwargInjector,
    _validate_ast,
)


def _injected_graph_attr(src: str) -> dict[str, str]:
    """Run the injector against a source snippet and return the dict literal
    it inlined as the `graph_attr` kwarg on the first Diagram() call. Used by
    every direction-aware default test."""
    tree = ast.parse(src)
    inj = _DiagramKwargInjector()
    inj.visit(tree)
    ast.fix_missing_locations(tree)
    # Find the Diagram call inside the first With.
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                ctx = item.context_expr
                if (isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Name)
                        and ctx.func.id == "Diagram"):
                    for kw in ctx.keywords:
                        if kw.arg == "graph_attr" and isinstance(kw.value, ast.Dict):
                            return {
                                k.value: v.value
                                for k, v in zip(kw.value.keys, kw.value.values)
                            }
    return {}


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


# ── direction-aware graph_attr defaults ───────────────────────────────────
#
# In Graphviz `nodesep` separates siblings along the rank axis (perpendicular
# to flow) and `ranksep` separates layers along the flow axis. Validator's
# _MIN_HORIZ_GAP=80 / _MIN_VERT_GAP=60 require >=80px horizontally; therefore
# the rank-perpendicular axis (the one that becomes horizontal) needs the
# larger value. TB/BT and LR/RL swap which is which.


def test_default_graph_attrs_tb_small():
    """3-node TB diagram: nodesep is horizontal, ranksep is vertical."""
    src = (
        "from diagrams import Diagram\n"
        "from diagrams.azure.compute import VM\n"
        "with Diagram('x', direction='TB'):\n"
        "    a = VM('a'); b = VM('b'); c = VM('c')\n"
    )
    attrs = _injected_graph_attr(src)
    assert attrs == {"nodesep": "1.0", "ranksep": "0.8"}


def test_default_graph_attrs_lr_small():
    """3-node LR diagram: ranksep is horizontal, nodesep is vertical."""
    src = (
        "from diagrams import Diagram\n"
        "from diagrams.azure.compute import VM\n"
        "with Diagram('x', direction='LR'):\n"
        "    a = VM('a'); b = VM('b'); c = VM('c')\n"
    )
    attrs = _injected_graph_attr(src)
    assert attrs == {"nodesep": "0.8", "ranksep": "1.0"}


def test_default_graph_attrs_no_direction_defaults_lr():
    """mingrammer's implicit default direction is LR; absent kwarg must
    behave the same as direction='LR'."""
    src = (
        "from diagrams import Diagram\n"
        "from diagrams.azure.compute import VM\n"
        "with Diagram('x'):\n"
        "    a = VM('a'); b = VM('b'); c = VM('c')\n"
    )
    attrs = _injected_graph_attr(src)
    assert attrs == {"nodesep": "0.8", "ranksep": "1.0"}


def test_default_graph_attrs_tb_bumped():
    """15 Call sites > 12-node threshold → bumped TB defaults."""
    calls = "\n    ".join(f"VM('n{i}')" for i in range(15))
    src = (
        "from diagrams import Diagram\n"
        "from diagrams.azure.compute import VM\n"
        f"with Diagram('x', direction='TB'):\n    {calls}\n"
    )
    attrs = _injected_graph_attr(src)
    assert attrs == {"nodesep": "1.5", "ranksep": "1.2"}


def test_default_graph_attrs_lr_bumped():
    """15 Call sites > 12-node threshold → bumped LR defaults."""
    calls = "\n    ".join(f"VM('n{i}')" for i in range(15))
    src = (
        "from diagrams import Diagram\n"
        "from diagrams.azure.compute import VM\n"
        f"with Diagram('x', direction='LR'):\n    {calls}\n"
    )
    attrs = _injected_graph_attr(src)
    assert attrs == {"nodesep": "1.2", "ranksep": "1.5"}


def test_default_graph_attrs_skipped_when_user_passed_graph_attr():
    """If the user already specified `graph_attr=...`, the injector must not
    overwrite it — the user's intent wins."""
    src = (
        "from diagrams import Diagram\n"
        "from diagrams.azure.compute import VM\n"
        "with Diagram('x', direction='LR', graph_attr={'rankdir': 'LR'}):\n"
        "    a = VM('a')\n"
    )
    attrs = _injected_graph_attr(src)
    # The user's graph_attr should be present unchanged; the injector should
    # not have added nodesep/ranksep defaults.
    assert attrs == {"rankdir": "LR"}


# ── telemetry counter ────────────────────────────────────────────────────


def test_validate_ast_increments_diagram_imports_counter_with_subnamespace():
    """`from diagrams.aws.compute import EC2` increments the counter under
    `namespace="aws/compute"` — the sub-namespace granularity is what drives
    the AWS coverage follow-up decision (analytics / ml / devtools / business)
    and the GCP per-namespace priorities. `sum by (tool)` over the label
    family recovers the cloud-level rollup."""
    before = _DIAGRAM_IMPORTS.labels(
        tool="generate_drawio_from_python", namespace="aws/compute"
    )._value.get()
    src = (
        "from diagrams import Diagram\n"
        "from diagrams.aws.compute import EC2\n"
        "with Diagram('x'):\n"
        "    EC2('web')\n"
    )
    assert _validate_ast(ast.parse(src)) is None
    after = _DIAGRAM_IMPORTS.labels(
        tool="generate_drawio_from_python", namespace="aws/compute"
    )._value.get()
    assert after - before == 1


def test_validate_ast_increments_separate_subnamespaces_independently():
    """A diagram script that pulls from two AWS sub-namespaces increments
    two distinct label combinations so per-sub-namespace usage is visible
    in /metrics (not collapsed into a single `namespace=aws` rollup)."""
    src = (
        "from diagrams import Diagram\n"
        "from diagrams.aws.compute import EC2\n"
        "from diagrams.aws.network import VPC\n"
        "with Diagram('x'):\n"
        "    EC2('w'); VPC('n')\n"
    )
    before_compute = _DIAGRAM_IMPORTS.labels(
        tool="generate_drawio_from_python", namespace="aws/compute"
    )._value.get()
    before_network = _DIAGRAM_IMPORTS.labels(
        tool="generate_drawio_from_python", namespace="aws/network"
    )._value.get()
    assert _validate_ast(ast.parse(src)) is None
    after_compute = _DIAGRAM_IMPORTS.labels(
        tool="generate_drawio_from_python", namespace="aws/compute"
    )._value.get()
    after_network = _DIAGRAM_IMPORTS.labels(
        tool="generate_drawio_from_python", namespace="aws/network"
    )._value.get()
    assert after_compute - before_compute == 1
    assert after_network - before_network == 1


def test_validate_ast_does_not_increment_for_root_diagrams_import():
    """`from diagrams import Diagram` has no namespace component (splits to
    ['diagrams']); the counter increment is gated on len(parts) >= 2 so the
    root import doesn't pollute the namespace label set."""
    before_labels = set(_DIAGRAM_IMPORTS._metrics.keys())
    src = (
        "from diagrams import Diagram\n"
        "with Diagram('x'): pass\n"
    )
    assert _validate_ast(ast.parse(src)) is None
    after_labels = set(_DIAGRAM_IMPORTS._metrics.keys())
    # No new label combination should appear from the root import alone.
    assert after_labels == before_labels


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
