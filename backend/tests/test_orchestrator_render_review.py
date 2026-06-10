"""Tests for the orchestrator's `_build_render_review_message`.

This is the function that turns a successful diagram-tool call into a
synthetic user message with the rendered PNG inlined for the model's next
turn. It used to require `args.filename` to end in `.drawio`, which broke
the new `generate_drawio_from_python` tool (which passes a stem). We
extended the function to auto-append `.drawio` if the extension is missing;
these tests pin that behaviour.
"""

import base64
from pathlib import Path

import pytest

from app.agent.orchestrator import _build_render_review_message


def _write_fake_png(out_dir: Path, stem: str) -> Path:
    """Drop a 1-byte file at output/<stem>.png so the function has something
    to base64-encode. The orchestrator doesn't care if it's a real PNG."""
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{stem}.png"
    p.write_bytes(b"\x00")
    return p


def test_returns_none_when_filename_missing():
    assert _build_render_review_message({}) is None
    assert _build_render_review_message({"filename": ""}) is None


def test_returns_none_for_unsupported_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_fake_png(tmp_path / "output", "diag")
    # PDF/SVG aren't sent through OpenAI vision — skipped intentionally.
    assert _build_render_review_message({"filename": "diag.drawio", "format": "pdf"}) is None
    assert _build_render_review_message({"filename": "diag.drawio", "format": "svg"}) is None


def test_returns_none_when_png_missing(tmp_path, monkeypatch):
    """File the agent claims doesn't actually exist on disk — bail silently
    rather than crashing the whole turn."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    assert _build_render_review_message({"filename": "ghost.drawio"}) is None


def test_accepts_filename_with_drawio_extension(tmp_path, monkeypatch):
    """The original drawio-tools convention: filename ends in `.drawio`,
    the rendered PNG sits next to it."""
    monkeypatch.chdir(tmp_path)
    _write_fake_png(tmp_path / "output", "spoke")

    msg = _build_render_review_message({"filename": "spoke.drawio"})

    assert msg is not None
    assert msg["role"] == "user"
    # The content is multi-part: text + image_url.
    parts = msg["content"]
    assert any(p["type"] == "text" for p in parts)
    assert any(p["type"] == "image_url" for p in parts)


def test_accepts_filename_as_stem_without_extension(tmp_path, monkeypatch):
    """`generate_drawio_from_python` passes filename as a STEM (no extension).
    The orchestrator must auto-append `.drawio` and still find the PNG.
    This is the exact change that wired the new tool into the image-attach
    flow — without this, the rendered PNG never reaches the chat."""
    monkeypatch.chdir(tmp_path)
    _write_fake_png(tmp_path / "output", "spoke")

    msg = _build_render_review_message({"filename": "spoke"})  # no .drawio!

    assert msg is not None
    # Confirm the function actually opened our fake PNG and base64'd it.
    image_part = next(p for p in msg["content"] if p["type"] == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    # "auto", not "high": the Structure echo is authoritative for presence;
    # the image is aesthetics-only, and "high" was 429 fuel on diagram turns.
    assert image_part["image_url"]["detail"] == "auto"
    # And the encoded bytes are non-empty.
    encoded = image_part["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == b"\x00"


def test_accepts_file_name_synonym(tmp_path, monkeypatch):
    """Smaller models sometimes pass `file_name` instead of `filename`; the
    helper tolerates the synonym so we don't lose an image just because of
    a kwarg variant."""
    monkeypatch.chdir(tmp_path)
    _write_fake_png(tmp_path / "output", "spoke")
    msg = _build_render_review_message({"file_name": "spoke"})
    assert msg is not None


def test_review_text_mentions_filename(tmp_path, monkeypatch):
    """The text part includes the filename so the model knows which file
    the image corresponds to in its next turn."""
    monkeypatch.chdir(tmp_path)
    _write_fake_png(tmp_path / "output", "spoke")
    msg = _build_render_review_message({"filename": "spoke"})
    text = next(p for p in msg["content"] if p["type"] == "text")["text"]
    # Filename should appear with the `.drawio` extension since that's the
    # source-of-truth file the user would edit.
    assert "spoke.drawio" in text
