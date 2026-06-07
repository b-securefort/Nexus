"""Structural Diagram IR → pixel-faithful draw.io.

A *new, separate* diagram path (does not touch generate_drawio_from_python or the
drawio-diagrammer tools). The IR is **structural**: it states containment,
ordering, edge semantics and style *tokens* — but the layout-placement logic
("PaaS sits outside the VNet") lives in the prompt/style layer that produces the
IR, never in this engine. See Documentation/DESIGN.md §5 (structural-IR diagram
tool) and the glossary terms "Diagram IR" and "Adornment".

Walking-skeleton status: the emitter renders an IR whose geometry is supplied by
hand (render-first). The deterministic box-layout engine that *computes* geometry
from structure is the next slice and is intentionally not here yet.
"""
