"""Exact text measurement for everything the engine renders.

The placer, router, label placer, and detectors all reason about label boxes;
until now those boxes came from per-character estimates (CHAR_W ≈ 6.2) that
drifted from what draw.io actually paints. This module is the single text
oracle: widths come from Arial advance tables (`_font_advances.py`, baked
offline by scripts/gen_font_advances.py — draw.io's Helvetica renders as Arial
on Windows/Chromium), at the exact pixel sizes the emitter writes:

  * node labels             — 12px regular (draw.io default vertex font)
  * container header labels — bold, 11 or 12px parsed from the catalog style
  * edge labels             — 10px regular (emit.py's edgeLabel cell)
  * adornment labels        — 10px on nodes (emit.py override), 12px on containers

Geometry consumers must call these helpers instead of multiplying by a char
width, so the measured world and the painted world stay one world.
"""

from __future__ import annotations

import re

from ._font_advances import ADV_BOLD, ADV_REGULAR, UPEM
from .catalog import CONTAINER_STYLES

NODE_LABEL_PX = 12.0
EDGE_LABEL_PX = 10.0

# Unknown characters (outside the baked table) measure as the table's
# alphanumeric average — wrong by a hair, never by a word.
_FALLBACK = {
    id(ADV_REGULAR): sum(ADV_REGULAR[c] for c in "abcdefghijklmnopqrstuvwxyz0123456789") / 36,
    id(ADV_BOLD): sum(ADV_BOLD[c] for c in "abcdefghijklmnopqrstuvwxyz0123456789") / 36,
}


def text_width(text: str, px: float, bold: bool = False) -> float:
    """Width in px of the widest line of `text` at font size `px`."""
    table = ADV_BOLD if bold else ADV_REGULAR
    fallback = _FALLBACK[id(table)]
    return max(
        (sum(table.get(ch, fallback) for ch in line) * px / UPEM
         for line in text.splitlines()),
        default=0.0,
    )


def node_label_width(label: str) -> float:
    return text_width(label, NODE_LABEL_PX)


def edge_label_width(label: str) -> float:
    return text_width(label, EDGE_LABEL_PX)


# Catalog container styles declare their own fontSize; headers are bold there.
_FONT_SIZE_RE = re.compile(r"fontSize=(\d+)")
_CONTAINER_PX = {
    token: float(m.group(1)) if (m := _FONT_SIZE_RE.search(style)) else 12.0
    for token, style in CONTAINER_STYLES.items()
}


def container_label_width(label: str, style_token: str) -> float:
    return text_width(label, _CONTAINER_PX.get(style_token, 12.0), bold=True)


def adornment_label_width(label: str, on_node: bool) -> float:
    return text_width(label, EDGE_LABEL_PX if on_node else NODE_LABEL_PX)
