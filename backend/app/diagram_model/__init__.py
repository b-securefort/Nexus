"""The semantic graph/view layer above the Diagram IR.

The IR (`app.diagram_ir`) is a *render contract*: one picture, fully decided.
This package holds what the IR deliberately doesn't: a `SemanticGraph` richer
than any one diagram (every resource and relation, with provider metadata),
and `View`s that project it into IR — a selection plus collapse rules, so L0
overviews and L1 drill-downs are different projections of ONE model and can't
drift apart. `azure_import` builds the graph from Azure Resource Graph rows,
shifting the agent's job from inventing topology to curating it.
"""

from .graph import Relation, Resource, SemanticGraph
from .view import View, project

__all__ = ["Relation", "Resource", "SemanticGraph", "View", "project"]
