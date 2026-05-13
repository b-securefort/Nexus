"""Tests for patch_drawio_cell tool and the underlying _patch_geometry helper."""

from pathlib import Path

import pytest

from app.auth.models import User
from app.tools import patch_drawio as patch_mod
from app.tools.patch_drawio import _patch_geometry, PatchDrawioCellTool


_USER = User(oid="t", email="t@t.com", display_name="t")


def _wrap(cells_xml: str) -> str:
    return f"""<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Page-1">
    <mxGraphModel dx="1200" dy="900" pageWidth="1200" pageHeight="900">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        {cells_xml}
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


# ── _patch_geometry unit tests ─────────────────────────────────────────────

def test_patch_replaces_existing_attributes():
    xml = _wrap("""
    <mxCell id="vm" value="VM"
      style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    """)
    new_xml, err = _patch_geometry(xml, "vm", {"x": 200, "y": 220})
    assert err is None
    assert 'x="200"' in new_xml
    assert 'y="220"' in new_xml
    # untouched attrs preserved
    assert 'width="64"' in new_xml
    assert 'height="64"' in new_xml


def test_patch_preserves_unrelated_cells_byte_for_byte():
    """Patching one cell must not reformat any other cell's whitespace or
    attribute order. This is the core reason for using regex over ET."""
    xml = _wrap("""
    <mxCell id="vm" value="VM"
      style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="kv" value="Key Vault"
      style="shape=image;image=img/lib/azure2/security/Key_Vaults.svg;"
      vertex="1" parent="1">
      <mxGeometry x="300" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    """)
    new_xml, err = _patch_geometry(xml, "vm", {"x": 200})
    assert err is None
    # The Key Vault cell must be byte-identical
    assert (
        '<mxCell id="kv" value="Key Vault"\n'
        '      style="shape=image;image=img/lib/azure2/security/Key_Vaults.svg;"\n'
        '      vertex="1" parent="1">\n'
        '      <mxGeometry x="300" y="100" width="64" height="64" as="geometry"/>\n'
        '    </mxCell>'
    ) in new_xml


def test_patch_inserts_missing_attribute():
    """If the geometry doesn't already declare width, the patch must add it."""
    xml = _wrap("""
    <mxCell id="vm" value="VM"
      style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" as="geometry"/>
    </mxCell>
    """)
    new_xml, err = _patch_geometry(xml, "vm", {"width": 80})
    assert err is None
    assert 'width="80"' in new_xml
    assert 'x="100"' in new_xml


def test_patch_unknown_cell_returns_error():
    xml = _wrap("")
    new_xml, err = _patch_geometry(xml, "missing", {"x": 10})
    assert new_xml is None
    assert "not found" in err


def test_patch_cell_without_geometry_returns_error():
    xml = _wrap("""
    <mxCell id="orphan" value="x" vertex="1" parent="1"></mxCell>
    """)
    new_xml, err = _patch_geometry(xml, "orphan", {"x": 10})
    assert new_xml is None
    assert "no <mxGeometry>" in err


def test_patch_only_modifies_named_cell_when_ids_share_prefix():
    """Cell `vm` must not match `vm-2` — id matching is exact."""
    xml = _wrap("""
    <mxCell id="vm" value="VM"
      style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="vm-2" value="VM 2"
      style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg;"
      vertex="1" parent="1">
      <mxGeometry x="300" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    """)
    new_xml, err = _patch_geometry(xml, "vm", {"x": 999})
    assert err is None
    # vm-2 must keep x=300
    assert 'id="vm-2"' in new_xml and 'x="300"' in new_xml
    # vm must have x=999
    # Find the vm cell explicitly
    vm_block = new_xml.split('id="vm-2"')[0]
    assert 'id="vm"' in vm_block and 'x="999"' in vm_block


def test_patch_renders_whole_numbers_without_decimal():
    xml = _wrap("""
    <mxCell id="vm" value="VM"
      style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    """)
    new_xml, err = _patch_geometry(xml, "vm", {"x": 196.0})
    assert err is None
    assert 'x="196"' in new_xml
    assert 'x="196.0"' not in new_xml


# ── PatchDrawioCellTool integration tests ──────────────────────────────────

def _xml_with_vm(x: int = 100) -> str:
    return _wrap(f"""
    <mxCell id="vnet" value="VNet"
      style="rounded=0;whiteSpace=wrap;html=1;fillColor=#EFF6FC;strokeColor=#0078D4;strokeWidth=2;"
      vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="600" height="400" as="geometry"/>
    </mxCell>
    <mxCell id="vm" value="VM"
      style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg;"
      vertex="1" parent="vnet">
      <mxGeometry x="{x}" y="80" width="64" height="64" as="geometry"/>
    </mxCell>
    """)


def test_tool_patches_file_and_runs_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(patch_mod, "_OUTPUT_DIR", tmp_path)
    target = tmp_path / "diag.drawio"
    target.write_text(_xml_with_vm(x=10), encoding="utf-8")

    tool = PatchDrawioCellTool()
    result = tool.execute(
        {"filename": "diag.drawio", "cell_id": "vm", "x": 80}, _USER,
    )
    assert "Patched" in result
    assert "x=80" in result
    assert "Auto-validation:" in result
    # Reading the file should reflect the patch
    assert 'x="80"' in target.read_text(encoding="utf-8")


def test_tool_rejects_no_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(patch_mod, "_OUTPUT_DIR", tmp_path)
    target = tmp_path / "diag.drawio"
    target.write_text(_xml_with_vm(), encoding="utf-8")

    tool = PatchDrawioCellTool()
    result = tool.execute({"filename": "diag.drawio", "cell_id": "vm"}, _USER)
    assert "Error" in result
    assert "at least one of x, y, width, height" in result


def test_tool_rejects_path_traversal():
    tool = PatchDrawioCellTool()
    result = tool.execute(
        {"filename": "../etc/passwd.drawio", "cell_id": "vm", "x": 1}, _USER,
    )
    assert "Error" in result


def test_tool_rejects_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(patch_mod, "_OUTPUT_DIR", tmp_path)
    tool = PatchDrawioCellTool()
    result = tool.execute(
        {"filename": "ghost.drawio", "cell_id": "vm", "x": 1}, _USER,
    )
    assert "Error" in result
    assert "not found" in result.lower()


def test_tool_reports_unknown_cell(tmp_path, monkeypatch):
    monkeypatch.setattr(patch_mod, "_OUTPUT_DIR", tmp_path)
    target = tmp_path / "diag.drawio"
    target.write_text(_xml_with_vm(), encoding="utf-8")

    tool = PatchDrawioCellTool()
    result = tool.execute(
        {"filename": "diag.drawio", "cell_id": "ghost", "x": 1}, _USER,
    )
    assert "Error" in result
    assert "not found" in result
