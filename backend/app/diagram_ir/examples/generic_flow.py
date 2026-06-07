"""A non-cloud generic flowchart — proves the engine + IR are provider-agnostic
(no Azure/AWS icons, just built-in flowchart shapes). Order-processing flow, TB."""

from app.diagram_ir.schema import Container, Diagram, Edge, Node


def build() -> Diagram:
    nodes = [
        Node(id="start", label="Order received", icon="shape/terminator"),
        Node(id="validate", label="Validate order", icon="shape/process"),
        Node(id="instock", label="In stock?", icon="shape/decision"),
        Node(id="charge", label="Charge payment", icon="shape/process"),
        Node(id="ship", label="Ship order", icon="shape/process"),
        Node(id="backorder", label="Create backorder", icon="shape/process"),
        Node(id="db", label="Orders DB", icon="shape/datastore"),
        Node(id="done", label="Complete", icon="shape/terminator"),
    ]
    edges = [
        Edge("start", "validate", "flow"),
        Edge("validate", "instock", "flow"),
        Edge("instock", "charge", "flow", "yes"),
        Edge("instock", "backorder", "flow", "no"),
        Edge("charge", "ship", "flow"),
        Edge("ship", "db", "flow"),
        Edge("backorder", "db", "flow"),
        Edge("db", "done", "flow"),
    ]
    return Diagram(title="Order processing (generic flow)", direction="TB",
                   nodes=nodes, edges=edges)
