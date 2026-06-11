"""Tests for incremental IR edits (apply_edits + the tool's sidecar flow),
the structural echo, and adornment-label geometry.

Built from the conv #352 failure: 12 full re-rolls because every iteration
re-emitted the whole IR from memory (losing nodes), and the model verified
structure by squinting at the PNG instead of reading it."""

import json
from pathlib import Path

import pytest

from app.auth.models import User
from app.diagram_ir.edits import apply_edits
from app.diagram_ir.labels import adornment_boxes, collect_text_obstacles
from app.diagram_ir.schema import Adornment, Container, Diagram, Node
from app.tools.generic import generate_structured_diagram as mod
from app.tools.generic.generate_structured_diagram import GenerateStructuredDiagramTool


def _user() -> User:
    return User(oid="dev", email="dev@local", display_name="dev")


def _base_ir() -> dict:
    return {
        "title": "t", "direction": "LR",
        "containers": [
            {"id": "vnet", "label": "VNet", "style": "vnet", "children": ["snet"]},
            {"id": "snet", "label": "Subnet", "style": "subnet", "parent": "vnet",
             "children": ["pe"]},
        ],
        "nodes": [
            {"id": "app", "label": "App", "icon": "azure/app_services"},
            {"id": "pe", "label": "PE", "icon": "azure/private_endpoint", "parent": "snet"},
        ],
        "edges": [{"source": "app", "target": "pe", "type": "private"}],
    }


# ── apply_edits unit tests ─────────────────────────────────────────────────

class TestAttachPreservesSiblingOrder:
    """Conv #359: a reorder op followed by a same-parent upsert in ONE batch —
    the upsert's parent re-attach must not bounce the child to the end of the
    sibling list (it silently undid the agent's correct far-hop fix, twice)."""

    def _spine_ir(self) -> dict:
        return {
            "title": "t", "direction": "LR",
            "containers": [
                {"id": "root", "style": "band", "layout": "row",
                 "children": ["clients", "spoke", "external", "hub"]},
                {"id": "clients", "style": "zone", "parent": "root", "children": ["u"]},
                {"id": "spoke", "style": "vnet", "parent": "root", "children": ["a"]},
                {"id": "external", "style": "zone", "parent": "root", "children": ["x"]},
                {"id": "hub", "style": "zone", "parent": "root", "children": ["f"]},
            ],
            "nodes": [
                {"id": "u", "icon": "shape/actor", "parent": "clients"},
                {"id": "a", "icon": "azure/app_services", "parent": "spoke"},
                {"id": "x", "icon": "shape/cloud", "parent": "external"},
                {"id": "f", "icon": "azure/firewalls", "parent": "hub"},
            ],
            "edges": [],
        }

    def test_same_parent_upsert_keeps_reordered_position(self):
        # The exact conv #359 batch shape: reorder root, then touch hub.
        ir, err = apply_edits(self._spine_ir(), [
            {"op": "upsert_container",
             "container": {"id": "root",
                           "children": ["clients", "hub", "spoke", "external"]}},
            {"op": "upsert_container",
             "container": {"id": "hub", "label": "Hub", "parent": "root"}},
        ])
        assert err is None
        root = next(c for c in ir["containers"] if c["id"] == "root")
        assert root["children"] == ["clients", "hub", "spoke", "external"]

    def test_same_parent_node_upsert_keeps_position(self):
        ir, err = apply_edits(self._spine_ir(), [
            {"op": "upsert_container",
             "container": {"id": "clients", "children": ["u"]}},
            {"op": "upsert_node", "node": {"id": "u", "label": "User", "parent": "clients"}},
        ])
        assert err is None
        clients = next(c for c in ir["containers"] if c["id"] == "clients")
        assert clients["children"] == ["u"]

    def test_actual_reparent_still_moves(self):
        ir, err = apply_edits(self._spine_ir(), [
            {"op": "upsert_container", "container": {"id": "hub", "parent": "spoke"}},
        ])
        assert err is None
        root = next(c for c in ir["containers"] if c["id"] == "root")
        spoke = next(c for c in ir["containers"] if c["id"] == "spoke")
        assert "hub" not in root["children"]
        assert spoke["children"] == ["a", "hub"]


class TestApplyEdits:
    def test_upsert_new_node_syncs_parent(self):
        ir, err = apply_edits(_base_ir(), [
            {"op": "upsert_node",
             "node": {"id": "kv", "label": "Key Vault", "icon": "azure/key_vaults",
                      "parent": "snet"}},
        ])
        assert err is None
        snet = next(c for c in ir["containers"] if c["id"] == "snet")
        assert "kv" in snet["children"]          # other side synced automatically

    def test_upsert_existing_node_reparents(self):
        ir, err = apply_edits(_base_ir(), [
            {"op": "upsert_node", "node": {"id": "pe", "parent": None}},
        ])
        assert err is None
        snet = next(c for c in ir["containers"] if c["id"] == "snet")
        assert "pe" not in snet["children"]
        pe = next(n for n in ir["nodes"] if n["id"] == "pe")
        assert pe["parent"] is None

    def test_update_label_only_keeps_everything_else(self):
        ir, err = apply_edits(_base_ir(), [
            {"op": "upsert_node", "node": {"id": "app", "label": "API App"}},
        ])
        assert err is None
        app = next(n for n in ir["nodes"] if n["id"] == "app")
        assert app["label"] == "API App"
        assert app["icon"] == "azure/app_services"   # untouched
        assert len(ir["edges"]) == 1                  # untouched

    def test_remove_node_drops_edges_and_child_refs(self):
        ir, err = apply_edits(_base_ir(), [{"op": "remove_node", "id": "pe"}])
        assert err is None
        assert all(n["id"] != "pe" for n in ir["nodes"])
        assert ir["edges"] == []
        snet = next(c for c in ir["containers"] if c["id"] == "snet")
        assert "pe" not in snet["children"]

    def test_remove_container_dissolves_children_to_grandparent(self):
        # conv #355: "still has children" errors cost 2 iterations per attempt;
        # the intent is always "remove the box, keep its contents".
        ir, err = apply_edits(_base_ir(), [{"op": "remove_container", "id": "snet"}])
        assert err is None
        assert all(c["id"] != "snet" for c in ir["containers"])
        vnet = next(c for c in ir["containers"] if c["id"] == "vnet")
        assert "pe" in vnet["children"]          # child climbed to grandparent
        pe = next(n for n in ir["nodes"] if n["id"] == "pe")
        assert pe["parent"] == "vnet"

    def test_remove_top_level_container_children_become_top_level(self):
        ir, err = apply_edits(_base_ir(), [{"op": "remove_container", "id": "vnet"}])
        assert err is None
        snet = next(c for c in ir["containers"] if c["id"] == "snet")
        assert snet["parent"] is None
        assert "pe" in snet["children"]          # its own children untouched

    def test_children_may_reference_containers_created_later_in_batch(self):
        # conv #355: forward references within one batch failed 3 times with
        # "children not found" — the model thinks of the batch as a transaction.
        ir, err = apply_edits(_base_ir(), [
            {"op": "upsert_container",
             "container": {"id": "vnet", "children": ["snet", "band"]}},
            {"op": "upsert_container",
             "container": {"id": "band", "label": "Band", "style": "band",
                           "children": []}},
        ])
        assert err is None
        band = next(c for c in ir["containers"] if c["id"] == "band")
        assert band["label"] == "Band"           # later upsert merged into shell
        assert band["parent"] == "vnet"
        vnet = next(c for c in ir["containers"] if c["id"] == "vnet")
        assert "band" in vnet["children"]

    def test_children_referencing_id_nothing_defines_still_errors(self):
        ir, err = apply_edits(_base_ir(), [
            {"op": "upsert_container",
             "container": {"id": "vnet", "children": ["snet", "ghost"]}},
        ])
        assert ir is None and "children not found" in err

    def test_upsert_edge_replaces_matching_pair(self):
        ir, err = apply_edits(_base_ir(), [
            {"op": "upsert_edge", "edge": {"source": "app", "target": "pe",
                                           "label": "private link"}},
        ])
        assert err is None
        assert len(ir["edges"]) == 1
        assert ir["edges"][0]["label"] == "private link"
        assert ir["edges"][0]["type"] == "private"    # merged, not replaced

    def test_remove_edge(self):
        ir, err = apply_edits(_base_ir(), [
            {"op": "remove_edge", "source": "app", "target": "pe"},
        ])
        assert err is None and ir["edges"] == []

    def test_set_direction(self):
        ir, err = apply_edits(_base_ir(), [{"op": "set", "direction": "TB"}])
        assert err is None and ir["direction"] == "TB"

    def test_unknown_op_and_field_rejected(self):
        ir, err = apply_edits(_base_ir(), [{"op": "teleport", "id": "app"}])
        assert ir is None and "unknown op" in err
        ir, err = apply_edits(_base_ir(), [
            {"op": "upsert_node", "node": {"id": "app", "color": "red"}},
        ])
        assert ir is None and "unknown node field" in err

    def test_error_aborts_whole_batch(self):
        original = _base_ir()
        ir, err = apply_edits(original, [
            {"op": "remove_edge", "source": "app", "target": "pe"},
            {"op": "remove_node", "id": "ghost"},
        ])
        assert ir is None and "ghost" in err
        assert len(original["edges"]) == 1     # input untouched on failure

    def test_new_node_without_icon_rejected(self):
        ir, err = apply_edits(_base_ir(), [
            {"op": "upsert_node", "node": {"id": "new", "label": "X"}},
        ])
        assert ir is None and "needs an icon" in err


# ── Tool-level: sidecar round-trip + echo ──────────────────────────────────

@pytest.fixture
def tool(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_OUTPUT_DIR", tmp_path)

    def fake_render(filename: str, fmt: str = "png"):
        png = tmp_path / Path(filename).with_suffix(f".{fmt}")
        png.write_bytes(b"\x89PNG\r\n\x1a\n stub")
        return png, "stub", None

    monkeypatch.setattr(mod, "render_drawio_to_disk", fake_render)
    return GenerateStructuredDiagramTool()


class TestToolEditsFlow:
    def test_first_render_persists_sidecar(self, tool, tmp_path):
        out = tool.execute({"filename": "arch", "diagram": _base_ir()}, _user())
        assert "Diagram rendered" in out
        sidecar = json.loads((tmp_path / "arch.ir.json").read_text(encoding="utf-8"))
        assert {n["id"] for n in sidecar["nodes"]} == {"app", "pe"}

    def test_edits_only_call_builds_on_stored_ir(self, tool, tmp_path):
        tool.execute({"filename": "arch", "diagram": _base_ir()}, _user())
        out = tool.execute({"filename": "arch", "edits": [
            {"op": "upsert_node",
             "node": {"id": "kv", "label": "Key Vault", "icon": "azure/key_vaults",
                      "parent": "snet"}},
            {"op": "upsert_edge", "edge": {"source": "app", "target": "kv",
                                           "type": "private"}},
        ]}, _user())
        assert "Diagram rendered" in out
        # Unchanged structure survived; the delta landed; sidecar advanced.
        sidecar = json.loads((tmp_path / "arch.ir.json").read_text(encoding="utf-8"))
        assert {n["id"] for n in sidecar["nodes"]} == {"app", "pe", "kv"}
        assert "kv" in out      # echo lists the new node

    def test_edits_without_stored_ir_rejected(self, tool):
        out = tool.execute({"filename": "fresh", "edits": [
            {"op": "remove_node", "id": "x"}]}, _user())
        assert out.startswith("Error:") and "first call" in out

    def test_failed_edit_leaves_sidecar_untouched(self, tool, tmp_path):
        tool.execute({"filename": "arch", "diagram": _base_ir()}, _user())
        before = (tmp_path / "arch.ir.json").read_text(encoding="utf-8")
        out = tool.execute({"filename": "arch", "edits": [
            {"op": "remove_node", "id": "ghost"}]}, _user())
        assert out.startswith("Error:")
        assert (tmp_path / "arch.ir.json").read_text(encoding="utf-8") == before

    def test_result_carries_structural_echo(self, tool):
        out = tool.execute({"filename": "arch", "diagram": _base_ir()}, _user())
        assert "Structure (authoritative" in out
        assert "nodes (2): app, pe" in out
        assert "app->pe" in out


# ── Adornment-label geometry ───────────────────────────────────────────────

class TestAdornmentLabels:
    def test_node_adornment_label_sits_beside_glyph_not_on_icon(self):
        n = Node(id="afd", label="Front Door", icon="azure/front_doors",
                 x=100, y=100, w=56, h=56,
                 adornments=[Adornment(icon="azure/firewalls",
                                       corner="top-right", label="WAF")])
        boxes = adornment_boxes(n)
        assert len(boxes) == 2                  # glyph + label
        glyph, label_box = boxes
        assert label_box[0] >= glyph[2]         # beside the glyph, not under it
        assert label_box[3] <= glyph[3] + 1     # vertically within the glyph band,
        # i.e. NOT rendered below into the icon body / caption (the old defect).

    def test_container_adornment_label_stays_below_glyph(self):
        c = Container(id="snet", label="Subnet", style="subnet",
                      x=0, y=0, w=300, h=120,
                      adornments=[Adornment(icon="azure/network_security_groups",
                                            corner="top-right", label="NSG")])
        glyph, label_box = adornment_boxes(c)
        assert label_box[1] == glyph[3]          # directly below the glyph

    def test_adornment_boxes_are_placer_obstacles(self):
        d = Diagram(nodes=[Node(
            id="afd", label="FD", icon="azure/front_doors", x=0, y=0,
            adornments=[Adornment(icon="azure/firewalls", corner="top-right",
                                  label="WAF")])])
        obs = collect_text_obstacles(d)
        assert len(obs) >= 4                     # icon + caption + glyph + adorn label

    def test_emit_places_node_adornment_label_to_the_side(self):
        from app.diagram_ir.emit import emit_drawio
        d = Diagram(nodes=[Node(
            id="afd", label="FD", icon="azure/front_doors", x=0, y=0,
            adornments=[Adornment(icon="azure/firewalls", corner="top-right",
                                  label="WAF")])])
        xml = emit_drawio(d)
        assert "labelPosition=right" in xml      # not below-glyph (= on the icon)
