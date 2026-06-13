"""Tests for the execute_script kill switch (DESIGN.md §5 2026-06-04).

A long-running script must be terminable when the user hits Stop / disconnects.
These cover the registry primitives, the conversation-id ContextVar, and a real
end-to-end process kill (cross-platform).
"""

import subprocess
import sys
import threading
import time

import pytest

from app.tools import base
from app.tools.base import (
    consume_stream,
    get_conversation_id,
    kill_conversation_processes,
    register_process,
    set_conversation_id,
    stream_subprocess,
    unregister_process,
    _process_registry,
)


def _spawn_sleeper() -> subprocess.Popen:
    """Spawn a 30s python sleeper in its own killable group, like execute_script."""
    kwargs = dict(base.SUBPROCESS_FLAGS)
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **kwargs,
    )


def _wait_dead(proc: subprocess.Popen, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return proc.poll() is not None


class TestConversationIdContextVar:
    def test_set_get_roundtrip(self):
        set_conversation_id(42)
        assert get_conversation_id() == 42
        set_conversation_id(None)
        assert get_conversation_id() is None


class TestProcessRegistry:
    def test_register_then_unregister(self):
        proc = _spawn_sleeper()
        try:
            register_process(123, proc)
            assert proc in _process_registry.get(123, set())
            unregister_process(123, proc)
            assert 123 not in _process_registry  # last one out clears the key
        finally:
            proc.kill()

    def test_none_conversation_is_noop(self):
        proc = _spawn_sleeper()
        try:
            register_process(None, proc)
            assert None not in _process_registry
            assert kill_conversation_processes(None) == 0
        finally:
            proc.kill()

    def test_kill_unknown_conversation_returns_zero(self):
        assert kill_conversation_processes(999_999) == 0


class TestKillSwitch:
    def test_kill_terminates_registered_process(self):
        proc = _spawn_sleeper()
        register_process(7, proc)
        assert proc.poll() is None  # running

        killed = kill_conversation_processes(7)

        assert killed == 1
        assert _wait_dead(proc), "process should be dead after kill"
        assert 7 not in _process_registry  # registry cleaned up

    def test_kill_handles_already_exited_process(self):
        proc = _spawn_sleeper()
        proc.kill()
        proc.wait(timeout=5)
        register_process(8, proc)
        # Should not raise even though the process is already gone.
        assert kill_conversation_processes(8) == 1
        assert 8 not in _process_registry

    def test_multiple_processes_one_conversation(self):
        p1, p2 = _spawn_sleeper(), _spawn_sleeper()
        register_process(9, p1)
        register_process(9, p2)
        assert kill_conversation_processes(9) == 2
        assert _wait_dead(p1) and _wait_dead(p2)
        assert 9 not in _process_registry


def _drain(gen):
    """Drive a stream_subprocess generator to completion; return (lines, result)."""
    lines = []
    while True:
        try:
            lines.append(next(gen))
        except StopIteration as stop:
            return lines, stop.value


class TestStreamSubprocessWatchdog:
    """Wall-clock watchdog for streaming subprocesses (DESIGN.md §5 2026-06-13).

    `for line in proc.stdout` blocks forever on a command that hangs without
    printing — the watchdog kills the tree at the deadline so the read hits
    EOF and the generator unwinds with timed_out=True.
    """

    def test_watchdog_kills_silent_hung_process(self):
        # Prints nothing, sleeps 30s — the exact shape the old code hung on.
        start = time.monotonic()
        gen = stream_subprocess(
            [sys.executable, "-c", "import time; time.sleep(30)"], timeout=1.5,
        )
        lines, res = _drain(gen)
        elapsed = time.monotonic() - start
        assert elapsed < 15, "watchdog did not fire — read loop hung"
        assert res.timed_out is True
        assert res.returncode != 0

    def test_normal_exit_is_not_a_timeout(self):
        gen = stream_subprocess(
            [sys.executable, "-c", "print('hello-stream')"], timeout=30,
        )
        lines, res = _drain(gen)
        assert res.timed_out is False
        assert res.returncode == 0
        assert "hello-stream" in res.stdout
        assert any("hello-stream" in line for line in lines)

    def test_stream_registers_for_kill_switch(self):
        """A streamed subprocess must be killable via the Stop path — print a
        line (so the generator yields and registration is observable), then
        kill the conversation's processes and check the run ends non-zero
        without the watchdog having fired."""
        set_conversation_id(77)
        try:
            gen = stream_subprocess(
                [sys.executable, "-c",
                 "import time; print('up', flush=True); time.sleep(30)"],
                timeout=60,
            )
            first = next(gen)  # registration happened before the first yield
            assert "up" in first
            assert len(_process_registry.get(77, set())) == 1

            killed = kill_conversation_processes(77)
            assert killed == 1

            _lines, res = _drain(gen)
            assert res.returncode != 0
            assert res.timed_out is False  # Stop-kill, not deadline-kill
            assert 77 not in _process_registry
        finally:
            set_conversation_id(None)

    def test_consume_stream_returns_generator_value(self):
        gen = stream_subprocess(
            [sys.executable, "-c", "print('drained')"], timeout=30,
        )
        # consume_stream is what collapsed execute() paths use (§5 2026-06-13)
        res = consume_stream(gen)
        assert res.returncode == 0
        assert "drained" in res.stdout
