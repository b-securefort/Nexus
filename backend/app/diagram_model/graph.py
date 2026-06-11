"""SemanticGraph: everything known about a topology, independent of any picture.

A `Resource` is anything that can appear in a diagram — a subscription, a
VNet, an app. Containment is a parent pointer (one tree, like the IR), but
unlike the IR a resource carries provider metadata (`rtype`, `meta`) and the
graph carries *every* relation — a View decides later what a given picture
shows. Ids must be stable across re-imports so a diagram edited against one
import survives a refresh.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Resource:
    id: str                        # stable slug, e.g. "rg_prod/vnet_hub"
    label: str
    rtype: str = ""                # provider type, e.g. "microsoft.web/sites"
    parent: str | None = None      # containment (one tree)
    icon: str = ""                 # catalog icon ref; "" = projection decides
    meta: dict = field(default_factory=dict)   # region, sku, tags, arm id, ...


@dataclass
class Relation:
    source: str
    target: str
    type: str = "flow"             # IR edge vocabulary: flow|private|dns|telemetry|replication
    label: str = ""


@dataclass
class SemanticGraph:
    title: str = ""
    resources: list[Resource] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)

    def by_id(self) -> dict[str, Resource]:
        return {r.id: r for r in self.resources}

    def children_of(self, rid: str | None) -> list[Resource]:
        return [r for r in self.resources if r.parent == rid]

    def descendants_of(self, rid: str) -> set[str]:
        """All resource ids under `rid` (not including it)."""
        out: set[str] = set()
        frontier = [rid]
        while frontier:
            cur = frontier.pop()
            for child in self.children_of(cur):
                if child.id not in out:
                    out.add(child.id)
                    frontier.append(child.id)
        return out
