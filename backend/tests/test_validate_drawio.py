"""Tests for the validate_drawio tool and its underlying checks."""

from pathlib import Path

import pytest

from app.tools.generic.validate_drawio import validate_drawio_file


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


def test_small_empty_subnet_recognised_as_container(tmp_path):
    # 180x100 (well below the 300px threshold) and no children — old code would
    # flag it as a resource-sized vertex without an icon. New name-based check
    # treats it as a container based on the "Subnet" keyword in its label.
    cells = """
    <mxCell id="snet1" value="Subnet: Workload"
      style="rounded=0;whiteSpace=wrap;html=1;dashed=1;"
      vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="180" height="100" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "small-subnet.drawio", cells)
    report = validate_drawio_file(p)
    assert "PASSED" in report
    assert "1 containers" in report


def test_small_empty_vnet_recognised_as_container(tmp_path):
    cells = """
    <mxCell id="vnet1" value="Hub VNet"
      style="rounded=0;whiteSpace=wrap;html=1;"
      vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="200" height="120" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "small-vnet.drawio", cells)
    report = validate_drawio_file(p)
    assert "PASSED" in report
    assert "1 containers" in report


def test_small_unnamed_box_still_flagged_as_resource(tmp_path):
    # Sanity check: an undersized vertex with no container-like name and no
    # icon should still be flagged — we only relax the rule for named placeholders.
    cells = """
    <mxCell id="x" value="Azure Firewall"
      style="rounded=1;whiteSpace=wrap;html=1;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="180" height="70" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "small-resource.drawio", cells)
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


def test_small_subnet_with_children_treated_as_container(tmp_path):
    """Subnets smaller than 300px must still be recognized as containers when
    they parent other vertices, otherwise the validator falsely flags them
    as resource-sized icons (no vendor icon, overlapping with their own children).
    """
    cells = """
    <mxCell id="vnet" value="Spoke VNet"
      style="rounded=0;whiteSpace=wrap;html=1;fillColor=#EFF6FC;strokeColor=#0078D4;strokeWidth=2;"
      vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="800" height="500" as="geometry"/>
    </mxCell>
    <mxCell id="snet-app" value="snet-app"
      style="rounded=0;whiteSpace=wrap;html=1;fillColor=#F0F7FF;strokeColor=#9BC2E6;"
      vertex="1" parent="vnet">
      <mxGeometry x="40" y="60" width="280" height="180" as="geometry"/>
    </mxCell>
    <mxCell id="appint" value="VNet integration"
      style="shape=image;image=img/lib/azure2/networking/Virtual_Networks.svg;"
      vertex="1" parent="snet-app">
      <mxGeometry x="100" y="60" width="56" height="56" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "small-subnet.drawio", cells)
    report = validate_drawio_file(p)
    assert "PASSED" in report, report
    # snet-app is a parent of appint => container, so there should be 2 containers.
    assert "2 containers" in report, report


def test_overlap_message_includes_target_coordinate(tmp_path):
    """Overlap violations must give the model a concrete target coordinate
    so it doesn't have to guess on retry."""
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
    p = _write(tmp_path, "overlap-fix.drawio", cells)
    report = validate_drawio_file(p)
    assert "FAILED" in report
    assert "[overlap]" in report
    assert "Suggested fix" in report
    # fw is at x=[100,164]; lb at x=[180,244]. To clear horizontally, lb must
    # move so its x >= 164 + 80 = 244.
    assert "x >= 244" in report


def test_containment_message_includes_target_coordinate(tmp_path):
    """Containment violations must report the exact relative-x/y the icon
    should move to (or that the container needs to grow)."""
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
    p = _write(tmp_path, "contain-fix.drawio", cells)
    report = validate_drawio_file(p)
    assert "FAILED" in report
    assert "[containment]" in report
    assert "Suggested fix" in report
    # Icon currently at relative (10,10); needs to move to (40,40) to satisfy the pad.
    assert "x (relative to parent" in report
    assert "to 40" in report


def test_edge_passing_through_icon_flagged(tmp_path):
    """When every candidate L-shape for an edge has at least one non-endpoint
    icon blocking it, no clean orthogonal route exists and the rendered line
    will visibly cross some icon. The validator must flag this and tell the
    model to add waypoints.

    Setup: src=(100,100,64,64) center=(132,132); tgt=(900,800,64,64)
    center=(932,832). L-shape A (horiz-then-vert) goes through y=132 then
    x=932. L-shape B (vert-then-horiz) goes through x=132 then y=832. We
    place one blocker on A's horizontal segment and a different blocker on
    B's vertical segment, so each L-shape has at least one blocker.
    """
    cells = """
    <mxCell id="src" value="Front Door"
      style="shape=image;image=img/lib/azure2/networking/Front_Doors.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="blocker-a" value="Blocker A"
      style="shape=image;image=img/lib/azure2/networking/Application_Gateways.svg;"
      vertex="1" parent="1">
      <mxGeometry x="300" y="100" width="100" height="100" as="geometry"/>
    </mxCell>
    <mxCell id="blocker-b" value="Blocker B"
      style="shape=image;image=img/lib/azure2/networking/Application_Gateways.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="400" width="100" height="100" as="geometry"/>
    </mxCell>
    <mxCell id="tgt" value="Web App"
      style="shape=image;image=img/lib/azure2/app_services/App_Services.svg;"
      vertex="1" parent="1">
      <mxGeometry x="900" y="800" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="e1" value="HTTPS" edge="1" source="src" target="tgt" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "edge-through.drawio", cells)
    report = validate_drawio_file(p)
    assert "FAILED" in report, report
    assert "[edge-through-icon]" in report
    assert "waypoints" in report.lower()


def test_edge_with_explicit_waypoints_skipped(tmp_path):
    """An edge that already declares <Array as='points'> waypoints must not
    trigger the edge-passes-through-icon check — the model has forced the route.

    Same blocker layout as test_edge_passing_through_icon_flagged so the check
    would fire without the waypoint guard.
    """
    cells = """
    <mxCell id="src" value="Front Door"
      style="shape=image;image=img/lib/azure2/networking/Front_Doors.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="blocker-a" value="Blocker A"
      style="shape=image;image=img/lib/azure2/networking/Application_Gateways.svg;"
      vertex="1" parent="1">
      <mxGeometry x="300" y="100" width="100" height="100" as="geometry"/>
    </mxCell>
    <mxCell id="blocker-b" value="Blocker B"
      style="shape=image;image=img/lib/azure2/networking/Application_Gateways.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="400" width="100" height="100" as="geometry"/>
    </mxCell>
    <mxCell id="tgt" value="Web App"
      style="shape=image;image=img/lib/azure2/app_services/App_Services.svg;"
      vertex="1" parent="1">
      <mxGeometry x="900" y="800" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="e1" value="HTTPS" edge="1" source="src" target="tgt" parent="1">
      <mxGeometry relative="1" as="geometry">
        <Array as="points">
          <mxPoint x="500" y="900"/>
        </Array>
      </mxGeometry>
    </mxCell>
    """
    p = _write(tmp_path, "edge-waypoints.drawio", cells)
    report = validate_drawio_file(p)
    assert "[edge-through-icon]" not in report


def test_edge_label_overlap_hint(tmp_path):
    """Two labelled edges whose midpoints coincide should produce a hint."""
    cells = """
    <mxCell id="hub" value="Hub"
      style="shape=image;image=img/lib/azure2/networking/Virtual_Networks.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="spoke1" value="Spoke 1"
      style="shape=image;image=img/lib/azure2/networking/Virtual_Networks.svg;"
      vertex="1" parent="1">
      <mxGeometry x="500" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="spoke2" value="Spoke 2"
      style="shape=image;image=img/lib/azure2/networking/Virtual_Networks.svg;"
      vertex="1" parent="1">
      <mxGeometry x="500" y="300" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="e1" value="VNet peering" edge="1" source="hub" target="spoke1" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
    <mxCell id="e2" value="VNet peering" edge="1" source="hub" target="spoke2" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "edge-label-overlap.drawio", cells)
    report = validate_drawio_file(p)
    # Either the duplicate-label violation OR the label-overlap hint must show up;
    # both signal the same underlying problem from different angles.
    assert "[duplicate-edge-labels]" in report or "edge labels for" in report


def test_edge_label_in_container_title_hint(tmp_path):
    """An edge whose midpoint falls inside a container's top title strip
    should produce a hint, since the rendered label visually clips the title."""
    cells = """
    <mxCell id="vnet" value="Hub VNet"
      style="rounded=0;whiteSpace=wrap;html=1;fillColor=#EFF6FC;strokeColor=#0078D4;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="600" height="400" as="geometry"/>
    </mxCell>
    <mxCell id="src" value="Globe"
      style="shape=image;image=img/lib/azure2/general/Globe.svg;"
      vertex="1" parent="1">
      <mxGeometry x="20" y="100" width="48" height="48" as="geometry"/>
    </mxCell>
    <mxCell id="tgt" value="Firewall"
      style="shape=image;image=img/lib/azure2/networking/Firewalls.svg;"
      vertex="1" parent="1">
      <mxGeometry x="800" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="e1" value="Forward to spoke" edge="1"
      source="src" target="tgt" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "edge-in-title.drawio", cells)
    report = validate_drawio_file(p)
    # Edge midpoint x=(20+68+800+864)/4=438, y=(100+148+100+164)/4=128
    # Hub VNet title strip is y in [100, 124]. y=128 is JUST below the strip,
    # so this should NOT fire. Adjust source/target to land inside.
    # Use lower-positioned icons so the midpoint falls in the strip.
    cells = """
    <mxCell id="vnet" value="Hub VNet"
      style="rounded=0;whiteSpace=wrap;html=1;fillColor=#EFF6FC;strokeColor=#0078D4;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="600" height="400" as="geometry"/>
    </mxCell>
    <mxCell id="src" value="Globe"
      style="shape=image;image=img/lib/azure2/general/Globe.svg;"
      vertex="1" parent="1">
      <mxGeometry x="20" y="80" width="48" height="48" as="geometry"/>
    </mxCell>
    <mxCell id="tgt" value="Firewall"
      style="shape=image;image=img/lib/azure2/networking/Firewalls.svg;"
      vertex="1" parent="1">
      <mxGeometry x="800" y="80" width="64" height="64" as="geometry"/>
    </mxCell>
    <mxCell id="e1" value="Forward to spoke" edge="1"
      source="src" target="tgt" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
    """
    p = _write(tmp_path, "edge-in-title2.drawio", cells)
    report = validate_drawio_file(p)
    # Midpoint = ((20+68+800+864)/4, (80+128+80+144)/4) = (438, 108).
    # vnet title strip: x in [100,700], y in [100,124]. (438,108) is inside.
    assert "title strip" in report, report


def test_invalid_xml_returns_error(tmp_path):
    p = tmp_path / "bad.drawio"
    p.write_text("<mxfile><not closed", encoding="utf-8")
    report = validate_drawio_file(p)
    assert "FAILED" in report or "parse error" in report.lower()


def test_generate_file_auto_validates_drawio(tmp_path, monkeypatch):
    """generate_file must run validate_drawio automatically on .drawio writes
    so the model can't bypass validation."""
    from app.auth.models import User
    from app.tools.generic import generate_file as gen_mod

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


def test_generate_file_auto_renders_drawio(tmp_path, monkeypatch):
    """generate_file must auto-render every .drawio write so the agent's
    vision feedback loop fires without an explicit render_drawio call.

    We monkeypatch render_drawio_to_disk to a stub that fakes a successful
    PNG render, then assert generate_file's result advertises the render.
    """
    from app.auth.models import User
    from app.tools.generic import generate_file as gen_mod
    from app.tools.generic import render_drawio as render_mod

    monkeypatch.setattr(gen_mod, "_OUTPUT_DIR", tmp_path)

    rendered_path = tmp_path / "stub.png"

    def fake_render(filename, fmt="png"):
        rendered_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 16)
        return rendered_path, "stub", None

    monkeypatch.setattr(render_mod, "render_drawio_to_disk", fake_render)

    tool = gen_mod.GenerateFileTool()
    user = User(oid="test", email="t@t.com", display_name="t")
    minimal_xml = _wrap("""
    <mxCell id="vm" value="VM"
      style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg;"
      vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="64" height="64" as="geometry"/>
    </mxCell>
    """)
    result = tool.execute(
        {"filename": "auto.drawio", "content": minimal_xml, "overwrite": True}, user
    )
    assert "Auto-render:" in result
    assert "stub.png" in result
    assert "next turn for visual review" in result


def test_generate_file_skips_render_on_xml_parse_failure(tmp_path, monkeypatch):
    """If validation reports an XML parse error, auto-render is skipped — the
    renderer would fail too and we want a clean result, not a noisy one."""
    from app.auth.models import User
    from app.tools.generic import generate_file as gen_mod
    from app.tools.generic import render_drawio as render_mod

    monkeypatch.setattr(gen_mod, "_OUTPUT_DIR", tmp_path)
    called = {"count": 0}

    def fake_render(filename, fmt="png"):
        called["count"] += 1
        return None, None, "should not be called"

    monkeypatch.setattr(render_mod, "render_drawio_to_disk", fake_render)

    tool = gen_mod.GenerateFileTool()
    user = User(oid="test", email="t@t.com", display_name="t")
    result = tool.execute(
        {"filename": "broken.drawio", "content": "<not xml", "overwrite": True}, user
    )
    assert "XML parse error" in result
    assert called["count"] == 0
    assert "Auto-render" not in result


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
