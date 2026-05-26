"""Hygiene tests for the phase-gating system. See app/phases.py.

These tests are the structural guarantees that keep gate cleanup easy:
  - Every gate is fully populated (no half-written entries)
  - Overdue gates fail CI so they can't linger past their review_by date
  - The /healthz contract stays stable for monitoring
  - No call-site bypasses the registry by reading settings.NEXUS_PHASE
    directly (which would defeat the mechanical-removal story)
  - is_tool_enabled / is_skill_enabled mappings reference real gate names
"""

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.config import get_settings
from app.phases import (
    PHASE_GATES,
    _SKILL_GATES,
    _TOOL_GATES,
    is_enabled,
    is_skill_enabled,
    is_tool_enabled,
    overdue_gates,
    phase_status,
)


# ── Registry hygiene ─────────────────────────────────────────────────────────

class TestRegistryHygiene:
    def test_every_gate_has_complete_metadata(self):
        """Half-written entries are how rot starts. Force every field."""
        for name, gate in PHASE_GATES.items():
            assert gate.min_phase >= 0, f"{name}: min_phase must be >= 0"
            assert gate.description.strip(), f"{name}: missing description"
            assert isinstance(gate.review_by, date), (
                f"{name}: review_by must be a date"
            )
            assert gate.removal_criteria.strip(), (
                f"{name}: missing removal_criteria"
            )
            assert gate.removal_action.strip(), (
                f"{name}: missing removal_action — required so the cleanup "
                f"PR knows exactly what to inline / delete"
            )

    def test_tool_gate_mappings_point_at_real_gates(self):
        """_TOOL_GATES values must each be a key in PHASE_GATES."""
        for tool_name, gate_name in _TOOL_GATES.items():
            assert gate_name in PHASE_GATES, (
                f"_TOOL_GATES['{tool_name}'] -> '{gate_name}' but that gate "
                f"is not registered in PHASE_GATES"
            )

    def test_skill_gate_mappings_point_at_real_gates(self):
        """_SKILL_GATES values must each be a key in PHASE_GATES."""
        for skill_name, gate_name in _SKILL_GATES.items():
            assert gate_name in PHASE_GATES, (
                f"_SKILL_GATES['{skill_name}'] -> '{gate_name}' but that "
                f"gate is not registered in PHASE_GATES"
            )

    def test_unknown_gate_name_raises(self):
        """is_enabled() raises on typos — keeps a 'gate off forever' bug
        from sneaking in via a misspelled key."""
        with pytest.raises(KeyError):
            is_enabled("does-not-exist")


# ── The tripwire ─────────────────────────────────────────────────────────────

class TestOverdueGates:
    def test_no_overdue_gates_at_full_phase(self):
        """A gate past its review_by AND currently unlocked at the running
        phase = the cleanup PR should have happened. This failing is the
        signal to run the per-gate removal playbook in gatesreadme.md.
        """
        overdue = overdue_gates()
        assert not overdue, (
            f"Phase gates past review_by AND currently active: {overdue}. "
            f"Run the removal playbook in gatesreadme.md, or extend "
            f"review_by with a recorded reason."
        )

    def test_overdue_detection_actually_works(self, monkeypatch):
        """Belt-and-braces: prove the tripwire is wired up by forcing one
        gate to be overdue and checking it gets flagged. Without this, a
        future refactor could silently break overdue_gates() and the
        no_overdue test above would keep passing on a stale check.
        """
        gate_name = next(iter(PHASE_GATES))
        gate = PHASE_GATES[gate_name]
        future_today = gate.review_by + timedelta(days=1)
        # Make sure the phase is unlocked so the gate counts as active
        monkeypatch.setenv("NEXUS_PHASE", str(max(gate.min_phase, 1)))
        get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
        # The function uses get_settings() which is a singleton; clear it
        import app.config as cfg
        cfg._settings = None
        try:
            overdue = overdue_gates(today=future_today)
            assert gate_name in overdue
        finally:
            cfg._settings = None  # reset singleton for subsequent tests


# ── /healthz contract ────────────────────────────────────────────────────────

class TestPhaseStatusContract:
    def test_phase_status_shape(self):
        status = phase_status()
        assert isinstance(status, dict)
        assert "phase" in status and isinstance(status["phase"], int)
        assert "gates" in status and isinstance(status["gates"], dict)
        for name in PHASE_GATES:
            assert name in status["gates"], (
                f"phase_status() missing entry for '{name}'"
            )
            entry = status["gates"][name]
            assert isinstance(entry["enabled"], bool)
            assert isinstance(entry["min_phase"], int)
            assert isinstance(entry["review_by"], str)


# ── Tool/skill gating behaviour ──────────────────────────────────────────────

class TestGatingBehaviour:
    def test_ungated_tool_always_enabled(self):
        """Tools not in _TOOL_GATES pass through unconditionally."""
        assert is_tool_enabled("read_kb_file") is True
        assert is_tool_enabled("search_kb") is True
        assert is_tool_enabled("fetch_ms_docs") is True

    def test_ungated_skill_always_enabled(self):
        """Skills not in _SKILL_GATES pass through unconditionally."""
        assert is_skill_enabled("kb-searcher") is True
        assert is_skill_enabled("some-future-skill") is True

    def test_gated_tool_off_below_min_phase(self, monkeypatch):
        """Drop the phase below az_cli's min_phase and check it's filtered."""
        import app.config as cfg
        monkeypatch.setenv("NEXUS_PHASE", "0")
        cfg._settings = None
        try:
            assert is_tool_enabled("az_cli") is False
            assert is_tool_enabled("az_resource_graph") is False
        finally:
            cfg._settings = None

    def test_gated_tool_on_at_min_phase(self, monkeypatch):
        """At the phase where az_resource_graph unlocks, it must be on."""
        import app.config as cfg
        monkeypatch.setenv("NEXUS_PHASE", "1")
        cfg._settings = None
        try:
            assert is_tool_enabled("az_resource_graph") is True
            assert is_tool_enabled("az_cli") is False  # still P2
        finally:
            cfg._settings = None

    def test_gated_skill_off_below_min_phase(self, monkeypatch):
        """chat-with-kb is P2; at P1 it must be off."""
        import app.config as cfg
        monkeypatch.setenv("NEXUS_PHASE", "1")
        cfg._settings = None
        try:
            assert is_skill_enabled("chat-with-kb") is False
            assert is_skill_enabled("architect") is False
        finally:
            cfg._settings = None


# ── No raw phase checks anywhere else ────────────────────────────────────────

class TestNoBypass:
    def test_no_raw_phase_checks_outside_phases_module(self):
        """Anyone reading NEXUS_PHASE directly outside app/phases.py is
        fighting the registry — their call-site won't get cleaned up
        mechanically when the day comes to delete this module.

        Exceptions:
          - app/phases.py owns the only legitimate raw lookup
          - app/config.py declares the setting itself
        """
        forbidden = re.compile(
            r"(?:settings|cfg|_s|conf|self)\s*\.\s*NEXUS_PHASE\b"
            r"|get_settings\(\)\s*\.\s*NEXUS_PHASE\b",
            re.IGNORECASE,
        )
        backend_root = Path(__file__).resolve().parent.parent / "app"
        allowed_files = {"phases.py", "config.py"}
        offenders: list[str] = []
        for path in backend_root.rglob("*.py"):
            if path.name in allowed_files:
                continue
            text = path.read_text(encoding="utf-8")
            if forbidden.search(text):
                offenders.append(str(path.relative_to(backend_root)))
        assert not offenders, (
            f"These files read NEXUS_PHASE directly — route them through "
            f"is_enabled() / is_tool_enabled() / is_skill_enabled() from "
            f"app/phases.py: {offenders}"
        )
