"""Incremental edits to a stored Diagram IR.

Why this exists: the tool was regenerate-from-scratch only, so every iteration
forced the model to re-emit the complete IR JSON from memory — and each
re-emission is a fresh sample that can silently lose or mutate structure
(conv #352: nodes drawn in attempt 3 vanished in attempt 4; 12 renders to
converge). With edits, iteration 2+ sends a small delta against the IR that
was actually accepted, so unchanged structure CANNOT drift.

Operates on the raw IR dict (the same shape `load_ir` accepts), returning a
new dict — the caller re-runs the full validate→layout→route→emit pipeline on
the result, so edits get exactly the same hard gate as a full IR.

Ops (each `{"op": ..., ...}`):
  set               {title?, direction?}
  upsert_node       {node: {...}}      id required; merges fields; syncs parent
  remove_node       {id}               drops it, its edges, and child refs
  upsert_container  {container: {...}} id required; merges; children REPLACE +
                                       re-parent when provided
  remove_container  {id}               dissolves it: children re-parent to ITS
                                       parent (top-level when none)
  upsert_edge       {edge: {...}}      replaces first source->target match, else appends
  remove_edge       {source, target}   removes ALL matching

Parent/children stay in sync automatically — an upsert never needs the caller
to also patch the other side of the relationship.

Order within a batch doesn't matter for existence: an upsert_container may list
`children` that a LATER upsert in the same batch creates. The batch is treated
as one transaction (conv #355: forward references cost 3 failed iterations).
"""

from __future__ import annotations

import copy

_NODE_KEYS = {"id", "label", "icon", "parent", "align_to", "adornments"}
_CONTAINER_KEYS = {"id", "label", "style", "parent", "children", "layout",
                   "grid_cols", "align_to", "adornments"}
_EDGE_KEYS = {"source", "target", "type", "label"}


def _find(items: list[dict], item_id: str) -> dict | None:
    return next((i for i in items if i.get("id") == item_id), None)


def _detach_from_parents(ir: dict, child_id: str) -> None:
    for c in ir["containers"]:
        kids = c.get("children")
        if kids and child_id in kids:
            kids.remove(child_id)


def _attach(ir: dict, child_id: str, parent_id: str | None) -> str | None:
    """Point both sides of the parent/children relationship at `parent_id`
    (None = top-level). Returns an error string or None."""
    _detach_from_parents(ir, child_id)
    if not parent_id:
        return None
    parent = _find(ir["containers"], parent_id)
    if parent is None:
        return f"parent '{parent_id}' is not an existing container"
    parent.setdefault("children", [])
    if child_id not in parent["children"]:
        parent["children"].append(child_id)
    return None


def _merge(existing: dict, incoming: dict, allowed: set[str], kind: str) -> str | None:
    unknown = set(incoming) - allowed
    if unknown:
        return f"unknown {kind} field(s): {', '.join(sorted(unknown))}"
    existing.update(incoming)
    return None


def apply_edits(ir: dict, edits: list) -> tuple[dict | None, str | None]:
    """Apply `edits` to a copy of `ir`. Returns (new_ir, None) or (None, error).
    First error aborts the whole batch — partial application would leave the
    stored IR and the model's mental model out of sync."""
    if not isinstance(edits, list) or not edits:
        return None, "edits must be a non-empty list of operations"
    ir = copy.deepcopy(ir if isinstance(ir, dict) else {})
    ir.setdefault("containers", [])
    ir.setdefault("nodes", [])
    ir.setdefault("edges", [])

    # Pre-pass — forward references. Create an empty shell for every id this
    # batch upserts that doesn't exist yet, so an earlier edit may reference a
    # container/node a later edit defines. The real upsert merges its fields
    # into the shell; ids nothing in the batch defines still error normally.
    # Node shells require the icon up front so the "new node needs an icon"
    # gate keeps working (icon-less new nodes take the unshelled error path).
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        if edit.get("op") == "upsert_container":
            cont = edit.get("container")
            if (isinstance(cont, dict) and cont.get("id")
                    and _find(ir["containers"], cont["id"]) is None
                    and _find(ir["nodes"], cont["id"]) is None):
                ir["containers"].append({"id": cont["id"], "children": []})
        elif edit.get("op") == "upsert_node":
            node = edit.get("node")
            if (isinstance(node, dict) and node.get("id") and node.get("icon")
                    and _find(ir["nodes"], node["id"]) is None
                    and _find(ir["containers"], node["id"]) is None):
                ir["nodes"].append({"id": node["id"], "icon": node["icon"]})

    for i, edit in enumerate(edits):
        if not isinstance(edit, dict) or "op" not in edit:
            return None, f"edits[{i}]: each edit needs an 'op' field"
        op = edit["op"]
        err = None

        if op == "set":
            for key in ("title", "direction"):
                if key in edit:
                    ir[key] = edit[key]
            extra = set(edit) - {"op", "title", "direction"}
            if extra:
                err = f"set only accepts title/direction, got: {', '.join(sorted(extra))}"

        elif op == "upsert_node":
            node = edit.get("node")
            if not isinstance(node, dict) or not node.get("id"):
                err = "upsert_node needs node.id"
            else:
                existing = _find(ir["nodes"], node["id"])
                if existing is None:
                    if _find(ir["containers"], node["id"]):
                        err = f"'{node['id']}' is a container — use upsert_container"
                    elif not node.get("icon"):
                        err = f"new node '{node['id']}' needs an icon"
                    else:
                        ir["nodes"].append(dict(node))
                        existing = ir["nodes"][-1]
                else:
                    err = _merge(existing, node, _NODE_KEYS, "node")
                if err is None and "parent" in node:
                    err = _attach(ir, node["id"], node.get("parent"))
                elif err is None and existing is ir["nodes"][-1] and existing.get("parent"):
                    # brand-new node that carried a parent
                    err = _attach(ir, node["id"], existing.get("parent"))

        elif op == "remove_node":
            node_id = edit.get("id")
            if not node_id or _find(ir["nodes"], node_id) is None:
                err = f"remove_node: no node '{node_id}'"
            else:
                ir["nodes"] = [n for n in ir["nodes"] if n["id"] != node_id]
                _detach_from_parents(ir, node_id)
                ir["edges"] = [e for e in ir["edges"]
                               if node_id not in (e.get("source"), e.get("target"))]

        elif op == "upsert_container":
            cont = edit.get("container")
            if not isinstance(cont, dict) or not cont.get("id"):
                err = "upsert_container needs container.id"
            else:
                existing = _find(ir["containers"], cont["id"])
                if existing is None:
                    if _find(ir["nodes"], cont["id"]):
                        err = f"'{cont['id']}' is a node — use upsert_node"
                    else:
                        new = dict(cont)
                        new.setdefault("children", [])
                        ir["containers"].append(new)
                        existing = new
                else:
                    new_children = cont.get("children")
                    err = _merge(existing, {k: v for k, v in cont.items() if k != "children"},
                                 _CONTAINER_KEYS, "container")
                    if err is None and new_children is not None:
                        # children REPLACES the list: re-parent every listed
                        # child here; former children fall to top-level.
                        all_ids = ({n["id"] for n in ir["nodes"]}
                                   | {c["id"] for c in ir["containers"]})
                        missing = [k for k in new_children if k not in all_ids]
                        if missing:
                            err = f"children not found: {', '.join(missing)}"
                        else:
                            for old_kid in list(existing.get("children", [])):
                                if old_kid not in new_children:
                                    kid = (_find(ir["nodes"], old_kid)
                                           or _find(ir["containers"], old_kid))
                                    if kid is not None:
                                        kid["parent"] = None
                            existing["children"] = []
                            for kid_id in new_children:
                                _attach(ir, kid_id, cont["id"])
                                kid = (_find(ir["nodes"], kid_id)
                                       or _find(ir["containers"], kid_id))
                                kid["parent"] = cont["id"]
                if err is None and "parent" in cont:
                    err = _attach(ir, cont["id"], cont.get("parent"))

        elif op == "remove_container":
            cont_id = edit.get("id")
            cont = _find(ir["containers"], cont_id) if cont_id else None
            if cont is None:
                err = f"remove_container: no container '{cont_id}'"
            else:
                # Dissolve, don't block: surviving children re-parent to the
                # removed container's parent (top-level when none). Requiring
                # the model to empty the box first cost 2 iterations per
                # attempt (conv #355: 4 such errors) for what is always the
                # same intent — "remove the box, keep its contents".
                grandparent = cont.get("parent") or None
                for kid_id in list(cont.get("children") or []):
                    err = _attach(ir, kid_id, grandparent)
                    if err:
                        break
                    kid = (_find(ir["nodes"], kid_id)
                           or _find(ir["containers"], kid_id))
                    if kid is not None:
                        kid["parent"] = grandparent
                if err is None:
                    ir["containers"] = [c for c in ir["containers"] if c["id"] != cont_id]
                    _detach_from_parents(ir, cont_id)
                    ir["edges"] = [e for e in ir["edges"]
                                   if cont_id not in (e.get("source"), e.get("target"))]

        elif op == "upsert_edge":
            edge = edit.get("edge")
            if not isinstance(edge, dict) or not edge.get("source") or not edge.get("target"):
                err = "upsert_edge needs edge.source and edge.target"
            else:
                unknown = set(edge) - _EDGE_KEYS
                if unknown:
                    err = f"unknown edge field(s): {', '.join(sorted(unknown))}"
                else:
                    match = next((e for e in ir["edges"]
                                  if e.get("source") == edge["source"]
                                  and e.get("target") == edge["target"]), None)
                    if match is None:
                        ir["edges"].append(dict(edge))
                    else:
                        match.update(edge)

        elif op == "remove_edge":
            src, tgt = edit.get("source"), edit.get("target")
            before = len(ir["edges"])
            ir["edges"] = [e for e in ir["edges"]
                           if not (e.get("source") == src and e.get("target") == tgt)]
            if len(ir["edges"]) == before:
                err = f"remove_edge: no edge {src}->{tgt}"

        else:
            err = (f"unknown op '{op}' (legal: set, upsert_node, remove_node, "
                   "upsert_container, remove_container, upsert_edge, remove_edge)")

        if err:
            return None, f"edits[{i}] ({op}): {err}"

    return ir, None
