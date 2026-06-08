"""Tests for the `generate_structured_diagram` tool surface.

The layout/route/emit engine has its own suite (test_diagram_ir.py). Here we
cover the *tool* contract: registration, the hard validation gate (a broken IR
must render nothing), schema errors, and the happy path writing a .drawio +
scorecard. PNG rendering is stubbed so the test does not depend on a draw.io
install; output is redirected to tmp_path so the suite leaves no files behind.
"""

import dataclasses
from pathlib import Path

import pytest

from app.auth.models import User
from app.diagram_ir.examples import image5_auto
from app.tools.base import TOOL_REGISTRY
from app.tools.generic import generate_structured_diagram as mod
from app.tools.generic.generate_structured_diagram import GenerateStructuredDiagramTool


def _user() -> User:
    return User(oid="dev", email="dev@local", display_name="dev")


@pytest.fixture
def tool(tmp_path, monkeypatch):
    """Tool instance with output redirected to tmp_path and rendering stubbed
    to 'succeed' by writing a placeholder PNG next to the .drawio."""
    monkeypatch.setattr(mod, "_OUTPUT_DIR", tmp_path)

    def fake_render(filename: str, fmt: str = "png"):
        png = tmp_path / Path(filename).with_suffix(f".{fmt}")
        png.write_bytes(b"\x89PNG\r\n\x1a\n stub")
        return png, "stub", None

    monkeypatch.setattr(mod, "render_drawio_to_disk", fake_render)
    return GenerateStructuredDiagramTool()


def test_tool_registered_and_safe():
    t = TOOL_REGISTRY.get("generate_structured_diagram")
    assert t is not None
    assert t.requires_approval is False        # pure generation, no approval gate
    assert t.is_diagram_tool is True


def test_happy_path_writes_drawio_and_scorecard(tool, tmp_path):
    ir = dataclasses.asdict(image5_auto.build())
    out = tool.execute({"filename": "arch", "diagram": ir}, _user())
    assert "Diagram rendered" in out
    assert "A(line-over-icon)=0" in out and "C(arrow-hidden)=0" in out
    drawio = (tmp_path / "arch.drawio").read_text(encoding="utf-8")
    assert drawio.startswith("<mxfile")
    assert "img/lib/azure2/app_services/App_Services.svg" in drawio   # icon resolved
    assert (tmp_path / "arch.png").exists()


def test_broken_ir_is_gated_no_file_written(tool, tmp_path):
    bad = {"containers": [{"id": "c", "style": "bogus", "children": ["n"]}],
           "nodes": [{"id": "n", "icon": "azure/mysql", "parent": "ghost"}]}
    out = tool.execute({"filename": "broken", "diagram": bad}, _user())
    assert out.startswith("Error:")
    assert "unknown style 'bogus'" in out
    assert "parent 'ghost' does not exist" in out
    assert not (tmp_path / "broken.drawio").exists()   # nothing rendered on a hard error


def test_schema_error_reports_field_path(tool):
    out = tool.execute({"filename": "x", "diagram": {"nodes": [{"label": "no id/icon"}]}}, _user())
    assert "schema invalid" in out
    assert "missing required field 'id'" in out


def test_bad_filename_rejected(tool):
    # '!' is outside the [A-Za-z0-9_-] charset (path separators are neutralized
    # by Path().stem, so they're safe rather than errors — like python_diagram).
    out = tool.execute({"filename": "bad!name", "diagram": {"nodes": []}}, _user())
    assert out.startswith("Error:") and "letters, digits" in out


def test_missing_diagram_rejected(tool):
    out = tool.execute({"filename": "x"}, _user())
    assert out.startswith("Error:") and "diagram" in out


def test_advisory_warnings_do_not_block(tool, tmp_path):
    # A lone isolated node: warns (isolated) but still renders.
    ir = {"nodes": [{"id": "lonely", "label": "L", "icon": "azure/mysql"}]}
    out = tool.execute({"filename": "warned", "diagram": ir}, _user())
    assert "Diagram rendered" in out
    assert "isolated" in out
    assert (tmp_path / "warned.drawio").exists()
