"""Tests for the validate_drawio tool and its underlying checks."""

from pathlib import Path

import pytest

from app.tools.validate_drawio import validate_drawio_file


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


def _write(tmp_path: Path, name: str, cells_xml: str) -> Path:
    p = tmp_path / name
    p.write_text(_wrap(cells_xml), encoding="utf-8")
    return p


def test_clean_diagram_passes(tmp_path):
    cells = """
    <mxCell id="zone" value="Hub VNet"
      style="rounded=0;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;strokeWidth=4;align=left;verticalAlign=top;"
      vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="600" height="400" as="geometry"/>
    </mxCell>
    <mxCell id="fw" value="Firewall"
      style="shape=image;image=img/lib/azure2/networking/Firewalls.svg;"
      vertex="1" parent="zone">
      <mxGeometry x="60" y="80" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="lb" value="Load Balancer"
      style="shape=image;image=img/lib/azure2/networking/Load_Balancers.svg;"
      vertex="1" parent="zone">
      <mxGeometry x="240" y="80" width="64" height="64" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "clean.drawio", cells)
    report = validate_drawio_file(p)
    assert "PASSED" in report
    assert "1 containers" in report


def test_literal_newline_flagged(tmp_path):
    cells = """
    <mxCell id="fd" value="Azure Front Door\\n(WAF enabled)"
      style="shape=image;image=img/lib/azure2/networking/Front_Doors.svg;"
      vertex="1" parent="1">
      <mxGeometry x="80" y="80" width="64" height="64" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "newline.drawio", cells)
    report = validate_drawio_file(p)
    assert "FAILED" in report
    assert "[encoding]" in report
    assert "&#10;" in report


def test_overlap_flagged(tmp_path):
    cells = """
    <mxCell id="fw" value="Firewall"
      style="shape=image;image=img/lib/azure2/networking/Firewalls.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="lb" value="Load Balancer"
      style="shape=image;image=img/lib/azure2/networking/Load_Balancers.svg;"
      vertex="1" parent="1">
      <mxGeometry x="180" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "overlap.drawio", cells)
    report = validate_drawio_file(p)
    assert "FAILED" in report
    assert "[overlap]" in report


def test_missing_vendor_icon_flagged(tmp_path):
    cells = """
    <mxCell id="fw" value="Azure Firewall"
      style="rounded=1;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="180" height="70" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "no-icon.drawio", cells)
    report = validate_drawio_file(p)
    assert "FAILED" in report
    assert "[icon-style]" in report


def test_observability_inside_vnet_flagged(tmp_path):
    cells = """
    <mxCell id="vnet" value="Hub VNet"
      style="rounded=0;whiteSpace=wrap;html=1;strokeWidth=4;align=left;verticalAlign=top;"
      vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="600" height="400" as="geometry"/>
    </mxCell>
    <mxCell id="mon" value="Azure Monitor"
      style="shape=image;image=img/lib/azure2/management_governance/Monitor.svg;"
      vertex="1" parent="vnet">
      <mxGeometry x="60" y="80" width="64" height="64" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "obs-in-vnet.drawio", cells)
    report = validate_drawio_file(p)
    assert "FAILED" in report
    assert "[observability-in-vnet]" in report


def test_containment_flagged(tmp_path):
    cells = """
    <mxCell id="vnet" value="Hub VNet"
      style="rounded=0;whiteSpace=wrap;html=1;strokeWidth=4;"
      vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="600" height="400" as="geometry"/>
    </mxCell>
    <mxCell id="fw" value="Firewall"
      style="shape=image;image=img/lib/azure2/networking/Firewalls.svg;"
      vertex="1" parent="vnet">
      <mxGeometry x="10" y="10" width="64" height="64" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "contain.drawio", cells)
    report = validate_drawio_file(p)
    assert "FAILED" in report
    assert "[containment]" in report


def test_duplicate_edge_labels_flagged(tmp_path):
    cells = """
    <mxCell id="fd" value="Front Door"
      style="shape=image;image=img/lib/azure2/networking/Front_Doors.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="a" value="App A"
      style="shape=image;image=img/lib/azure2/app_services/App_Services.svg;"
      vertex="1" parent="1">
      <mxGeometry x="400" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="b" value="App B"
      style="shape=image;image=img/lib/azure2/app_services/App_Services.svg;"
      vertex="1" parent="1">
      <mxGeometry x="400" y="300" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="e1" value="HTTPS" edge="1" source="fd" target="a" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
    <mxCell id="e2" value="HTTPS" edge="1" source="fd" target="b" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "dup-labels.drawio", cells)
    report = validate_drawio_file(p)
    assert "FAILED" in report
    assert "[duplicate-edge-labels]" in report


def test_invalid_xml_returns_error(tmp_path):
    p = tmp_path / "bad.drawio"
    p.write_text("<mxfile><not closed", encoding="utf-8")
    report = validate_drawio_file(p)
    assert "FAILED" in report or "parse error" in report.lower()


def test_generate_file_auto_validates_drawio(tmp_path, monkeypatch):
    """generate_file must run validate_drawio automatically on .drawio writes
    so the model can't bypass validation."""
    from app.auth.models import User
    from app.tools import generate_file as gen_mod

    monkeypatch.setattr(gen_mod, "_OUTPUT_DIR", tmp_path)
    tool = gen_mod.GenerateFileTool()
    user = User(oid="test", email="t@t.com", display_name="t")

    bad_xml = _wrap("""
    <mxCell id="fw" value="Firewall\\n(Premium)"
      style="rounded=1;whiteSpace=wrap;html=1;fillColor=#fff2cc;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="180" height="70" as="geometry"/>
    </mxCell>
    """)
    result = tool.execute(
        {"filename": "auto.drawio", "content": bad_xml, "overwrite": True}, user
    )
    assert "Auto-validation:" in result
    assert "FAILED" in result
    assert "[encoding]" in result
    assert "[icon-style]" in result


def test_numbered_badges_and_text_labels_are_not_flagged_as_resources(tmp_path):
    """Decorative shapes (numbered flow badges, text labels, small callouts)
    must not trigger [icon-style] or [overlap] — they're not Azure resources."""
    cells = """
    <mxCell id="zone" value="VNet"
      style="rounded=0;whiteSpace=wrap;html=1;fillColor=#EFF6FC;strokeColor=#0078D4;"
      vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="600" height="400" as="geometry"/>
    </mxCell>
    <mxCell id="vm" value="VM"
      style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg;"
      vertex="1" parent="zone">
      <mxGeometry x="80" y="80" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="title" value="My Architecture"
      style="text;html=1;strokeColor=none;fillColor=none;align=left;fontStyle=1;fontSize=16;"
      vertex="1" parent="1">
      <mxGeometry x="40" y="10" width="500" height="24" as="geometry"/>
    </mxCell>
    <mxCell id="badge-1" value="1"
      style="ellipse;aspect=fixed;fillColor=#107C10;fontColor=#FFFFFF;strokeColor=none;fontStyle=1;fontSize=11;align=center;verticalAlign=middle;html=1;"
      vertex="1" parent="1">
      <mxGeometry x="200" y="200" width="26" height="26" as="geometry"/>
    </mxCell>
    <mxCell id="badge-2" value="2"
      style="ellipse;aspect=fixed;fillColor=#107C10;fontColor=#FFFFFF;strokeColor=none;fontStyle=1;fontSize=11;align=center;verticalAlign=middle;html=1;"
      vertex="1" parent="1">
      <mxGeometry x="220" y="200" width="26" height="26" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "deco.drawio", cells)
    report = validate_drawio_file(p)
    assert "PASSED" in report, f"Expected PASSED, got: {report}"
    assert "[icon-style]" not in report
    assert "[overlap]" not in report


def test_hint_managed_identity_in_subnet(tmp_path):
    """Architectural hint: Managed Identity should not be inside a VNet/subnet."""
    cells = """
    <mxCell id="vnet" value="Spoke VNet"
      style="rounded=0;whiteSpace=wrap;html=1;fillColor=#EFF6FC;strokeColor=#0078D4;"
      vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="600" height="400" as="geometry"/>
    </mxCell>
    <mxCell id="snet-app" value="App subnet"
      style="rounded=0;whiteSpace=wrap;html=1;fillColor=#F0F7FF;strokeColor=#9BC2E6;"
      vertex="1" parent="vnet">
      <mxGeometry x="40" y="60" width="500" height="300" as="geometry"/>
    </mxCell>
    <mxCell id="webapp" value="Web App"
      style="shape=image;image=img/lib/azure2/app_services/App_Services.svg;"
      vertex="1" parent="snet-app">
      <mxGeometry x="80" y="80" width="56" height="56" as="geometry"/>
    </mxCell>
    <mxCell id="mi" value="Managed Identity"
      style="shape=image;image=img/lib/azure2/identity/Managed_Identities.svg;"
      vertex="1" parent="snet-app">
      <mxGeometry x="240" y="80" width="48" height="48" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "mi-in-subnet.drawio", cells)
    report = validate_drawio_file(p)
    # Structural validation should still pass; the architectural issue is a hint.
    assert "PASSED" in report or "FAILED" in report
    assert "[hint]" in report
    assert "Managed Identity" in report or "identity-plane" in report
    assert "mi" in report


def test_hint_paas_in_subnet(tmp_path):
    """Architectural hint: a Web App icon parented inside a subnet."""
    cells = """
    <mxCell id="vnet" value="VNet"
      style="rounded=0;whiteSpace=wrap;html=1;fillColor=#EFF6FC;strokeColor=#0078D4;"
      vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="600" height="400" as="geometry"/>
    </mxCell>
    <mxCell id="snet" value="App subnet"
      style="rounded=0;whiteSpace=wrap;html=1;fillColor=#F0F7FF;strokeColor=#9BC2E6;"
      vertex="1" parent="vnet">
      <mxGeometry x="40" y="60" width="500" height="300" as="geometry"/>
    </mxCell>
    <mxCell id="webapp" value="Web App"
      style="shape=image;image=img/lib/azure2/app_services/App_Services.svg;"
      vertex="1" parent="snet">
      <mxGeometry x="80" y="80" width="56" height="56" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "paas-in-subnet.drawio", cells)
    report = validate_drawio_file(p)
    assert "[hint]" in report
    assert "PaaS" in report


def test_hint_badge_collision_with_edge_label(tmp_path):
    """Hint: a numbered badge sitting where an edge label will render."""
    cells = """
    <mxCell id="a" value="A"
      style="shape=image;image=img/lib/azure2/general/Globe.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="48" height="48" as="geometry"/>
    </mxCell>
    <mxCell id="b" value="B"
      style="shape=image;image=img/lib/azure2/general/Globe.svg;"
      vertex="1" parent="1">
      <mxGeometry x="500" y="100" width="48" height="48" as="geometry"/>
    </mxCell>
    <mxCell id="e1" value="Important Label"
      style="edgeStyle=orthogonalEdgeStyle;html=1;"
      edge="1" parent="1" source="a" target="b">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
    <mxCell id="badge-1" value="1"
      style="ellipse;aspect=fixed;fillColor=#107C10;fontColor=#FFFFFF;strokeColor=none;"
      vertex="1" parent="1">
      <mxGeometry x="312" y="111" width="26" height="26" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "badge-collide.drawio", cells)
    report = validate_drawio_file(p)
    assert "[hint]" in report
    assert "badge-1" in report
    assert "label-render area" in report or "visually collide" in report


def test_hint_orphan_badge(tmp_path):
    """Hint: a badge floating far away from any resource icon."""
    cells = """
    <mxCell id="a" value="A"
      style="shape=image;image=img/lib/azure2/general/Globe.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="48" height="48" as="geometry"/>
    </mxCell>
    <mxCell id="badge-orphan" value="9"
      style="ellipse;aspect=fixed;fillColor=#107C10;fontColor=#FFFFFF;strokeColor=none;"
      vertex="1" parent="1">
      <mxGeometry x="1500" y="1000" width="26" height="26" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "badge-orphan.drawio", cells)
    report = validate_drawio_file(p)
    assert "[hint]" in report
    assert "badge-orphan" in report
    assert "200px" in report or "floating" in report or "empty space" in report
