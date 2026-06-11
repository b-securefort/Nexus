"""textmetrics: exact Arial-table text measurement (replaces CHAR_W estimates)."""

from app.diagram_ir.textmetrics import (
    adornment_label_width,
    container_label_width,
    edge_label_width,
    node_label_width,
    text_width,
)


class TestTextWidth:
    def test_known_arial_advances(self):
        # Arial at upem=2048: 'H'=1479, 'i'=455 units. At 12px → ×12/2048.
        assert text_width("H", 12) == 1479 * 12 / 2048
        assert text_width("i", 12) == 455 * 12 / 2048

    def test_proportional_not_monospace(self):
        # The whole point: 'iiii' is far narrower than 'WWWW'. A CHAR_W
        # estimate gives them identical widths.
        assert text_width("WWWW", 12) < text_width("iiiiiiiiiiii", 12) * 3
        assert text_width("iiii", 12) < text_width("WWWW", 12) / 2

    def test_bold_wider_than_regular(self):
        s = "Gateway subnet"
        assert text_width(s, 12, bold=True) > text_width(s, 12)

    def test_scales_linearly_with_px(self):
        assert text_width("abc", 24) == 2 * text_width("abc", 12)

    def test_multiline_takes_widest_line(self):
        assert text_width("ab\nabcdef", 12) == text_width("abcdef", 12)

    def test_empty_and_unknown_chars(self):
        assert text_width("", 12) == 0.0
        # A char outside the baked table measures via the fallback, not 0/crash.
        assert text_width("中", 12) > 0


class TestContextHelpers:
    def test_node_label_is_12px_regular(self):
        assert node_label_width("App Service") == text_width("App Service", 12)

    def test_edge_label_is_10px_regular(self):
        assert edge_label_width("HTTPS") == text_width("HTTPS", 10)

    def test_container_label_px_parsed_from_catalog(self):
        # vnet declares fontSize=12, subnet fontSize=11 — both bold.
        assert container_label_width("Hub", "vnet") == text_width("Hub", 12, bold=True)
        assert container_label_width("Hub", "subnet") == text_width("Hub", 11, bold=True)
        # unknown token falls back to 12px bold rather than crashing
        assert container_label_width("Hub", "nope") == text_width("Hub", 12, bold=True)

    def test_adornment_label_px_depends_on_owner(self):
        assert adornment_label_width("WAF", on_node=True) == text_width("WAF", 10)
        assert adornment_label_width("WAF", on_node=False) == text_width("WAF", 12)
