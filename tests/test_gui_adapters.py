"""Tests for the GUI-chat adapters.

No test drives a real window. The automation layer is mocked exactly the way the
CLI-adapter tests mock ``subprocess.run``: the three physical actions
(``_focus_window``/``_submit_prompt``/``_read_response``) are patched, and the
clock (``_now``/``_sleep``) is replaced with a deterministic fake so polling runs
instantly.
"""

from __future__ import annotations

import pytest

from conductor.adapters import (
    AdapterError,
    ClaudeDesktopAdapter,
    GuiChatAdapter,
    _import_module,
    _poll_until_stable,
)


class _FakeClock:
    """Deterministic clock: ``sleep`` advances ``now`` instead of waiting."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _reader(values: list[str]):
    """Return a read() that yields each value once, then repeats the last."""

    def read() -> str:
        if len(values) > 1:
            return values.pop(0)
        return values[0] if values else ""

    return read


class _FakeGuiAdapter(GuiChatAdapter):
    """Concrete GuiChatAdapter whose physical actions are scripted, not real."""

    tool_name = "fake-gui"

    def __init__(self, responses: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.focused = False
        self.submitted: list[str] = []
        self._read = _reader(responses)
        clock = _FakeClock()
        self._now = clock.now
        self._sleep = clock.sleep

    def _focus_window(self) -> None:
        self.focused = True

    def _submit_prompt(self, prompt: str) -> None:
        self.submitted.append(prompt)

    def _read_response(self) -> str:
        return self._read()


# --- _poll_until_stable (the response-complete heuristic) ------------------


def _poll(read, *, overall_timeout=100.0, stable_for=2.0, poll_interval=1.0):
    clock = _FakeClock()
    return _poll_until_stable(
        read,
        overall_timeout=overall_timeout,
        stable_for=stable_for,
        poll_interval=poll_interval,
        now=clock.now,
        sleep=clock.sleep,
        tool_name="test",
    )


def test_poll_returns_text_once_it_stops_changing():
    read = _reader(["", "Hel", "Hello", "Hello", "Hello", "Hello"])
    assert _poll(read, stable_for=2.0, poll_interval=1.0) == "Hello"


def test_poll_ignores_empty_text_and_times_out_if_nothing_arrives():
    with pytest.raises(AdapterError, match="did not finish responding within"):
        _poll(_reader([""]), overall_timeout=5.0, poll_interval=1.0)


def test_poll_times_out_when_text_never_stabilises():
    counter = {"n": 0}

    def read() -> str:
        counter["n"] += 1
        return f"chunk-{counter['n']}"  # always different => never stable

    with pytest.raises(AdapterError, match="did not finish responding"):
        _poll(read, overall_timeout=5.0, stable_for=2.0, poll_interval=1.0)


def test_poll_requires_the_full_stable_window():
    # Changes at t=0,1; stable from t=1. With stable_for=3 it must not return
    # early at t=2, only once 3s have passed unchanged.
    read = _reader(["a", "b", "b", "b", "b", "b"])
    assert _poll(read, stable_for=3.0, poll_interval=1.0) == "b"


# --- GuiChatAdapter.send orchestration -------------------------------------


def test_send_focuses_submits_then_returns_stable_response():
    adapter = _FakeGuiAdapter(["", "partial", "full answer", "full answer", "full answer"])
    adapter.stable_for = 2.0
    adapter.poll_interval = 1.0

    result = adapter.send("hello there")

    assert result == "full answer"
    assert adapter.focused is True
    assert adapter.submitted == ["hello there"]


def test_send_rejects_empty_prompt():
    adapter = _FakeGuiAdapter(["x", "x", "x"])
    with pytest.raises(AdapterError, match="prompt is empty"):
        adapter.send("   ")


def test_send_times_out_when_no_response(monkeypatch):
    adapter = _FakeGuiAdapter([""])
    adapter.overall_timeout = 5.0
    adapter.poll_interval = 1.0
    with pytest.raises(AdapterError, match="did not finish responding"):
        adapter.send("anything")


def test_send_wraps_focus_failure_in_adapter_error():
    class _BadFocus(_FakeGuiAdapter):
        def _focus_window(self) -> None:
            raise RuntimeError("no window")

    adapter = _BadFocus(["x", "x", "x"])
    with pytest.raises(AdapterError, match="automation failed: no window") as exc_info:
        adapter.send("hi")
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_send_passes_through_adapter_error_from_focus():
    class _BadFocus(_FakeGuiAdapter):
        def _focus_window(self) -> None:
            raise AdapterError("Claude Desktop window not found")

    adapter = _BadFocus(["x", "x", "x"])
    with pytest.raises(AdapterError, match="window not found"):
        adapter.send("hi")


def test_send_wraps_submit_failure_in_adapter_error():
    class _BadSubmit(_FakeGuiAdapter):
        def _submit_prompt(self, prompt: str) -> None:
            raise OSError("clipboard busy")

    adapter = _BadSubmit(["x", "x", "x"])
    with pytest.raises(AdapterError, match="automation failed"):
        adapter.send("hi")


def test_send_wraps_raw_read_error_in_adapter_error():
    class _BadRead(_FakeGuiAdapter):
        def _read_response(self) -> str:
            raise RuntimeError("tree gone")

    adapter = _BadRead(["x"])
    with pytest.raises(AdapterError, match="failed to read response") as exc_info:
        adapter.send("hi")
    assert isinstance(exc_info.value.__cause__, RuntimeError)


# --- _import_module (lazy optional dependency guard) ------------------------


def test_import_module_returns_real_module():
    assert _import_module("json", "x").__name__ == "json"


def test_import_module_missing_raises_adapter_error():
    with pytest.raises(AdapterError, match="needs the 'no_such_pkg_xyz' package"):
        _import_module("no_such_pkg_xyz", "Claude Desktop")


# --- ClaudeDesktopAdapter seams (mock pywinauto/pyperclip) -----------------


def test_focus_window_missing_dependency_raises(monkeypatch):
    # Simulate pywinauto not installed: the lazy import must surface AdapterError.
    monkeypatch.setattr(
        "conductor.adapters._import_module",
        lambda name, tool: (_ for _ in ()).throw(
            AdapterError(f"{tool} needs the {name!r} package")
        ),
    )
    adapter = ClaudeDesktopAdapter()
    with pytest.raises(AdapterError, match="needs the 'pywinauto' package"):
        adapter._focus_window()


def test_read_response_returns_empty_before_window_focused():
    adapter = ClaudeDesktopAdapter()
    assert adapter._read_response() == ""


def test_claude_desktop_defaults():
    adapter = ClaudeDesktopAdapter()
    assert adapter.tool_name == "Claude Desktop"
    assert adapter.window_title_re == r"^Claude"
    assert adapter.overall_timeout == 120.0
    assert adapter.stable_for == 2.0
    assert adapter.poll_interval == 0.5
