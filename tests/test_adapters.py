"""Tests for the adapter layer. Subprocess is always mocked — no test ever
invokes the real claude / code / antigravity-ide binaries."""

from __future__ import annotations

import subprocess

import pytest

from conductor.adapters import (
    AdapterError,
    AntigravityEditorAdapter,
    ClaudeCodeAdapter,
    EchoAdapter,
    VSCodeAdapter,
    _clean,
    _split_args,
)

RUN_TARGET = "conductor.adapters.subprocess.run"


def _completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class _FakeRun:
    """Stand-in for ``subprocess.run`` that records calls and returns/raises."""

    def __init__(self, result=None, exc: BaseException | None = None) -> None:
        self.result = result
        self.exc = exc
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.result

    @property
    def last_cmd(self) -> list[str]:
        return self.calls[-1][0][0]

    @property
    def last_kwargs(self) -> dict:
        return self.calls[-1][1]


# --- EchoAdapter -----------------------------------------------------------


def test_echo_adapter_returns_prompt_unchanged():
    assert EchoAdapter().send("hello world") == "hello world"


# --- helpers ---------------------------------------------------------------


def test_clean_strips_ansi_and_whitespace():
    assert _clean("\x1b[31m  PONG \x1b[0m\n\n") == "PONG"


def test_split_args_simple():
    assert _split_args("--diff a.txt b.txt") == ["--diff", "a.txt", "b.txt"]


def test_split_args_preserves_windows_path_with_spaces():
    assert _split_args(r'--diff "C:\My Docs\a.txt" b.txt') == [
        "--diff",
        r"C:\My Docs\a.txt",
        "b.txt",
    ]


def test_split_args_empty_string():
    assert _split_args("") == []


# --- ClaudeCodeAdapter (mocked) -------------------------------------------


def test_claude_success_returns_clean_stdout(monkeypatch):
    fake = _FakeRun(result=_completed(0, stdout="PONG\n"))
    monkeypatch.setattr(RUN_TARGET, fake)

    assert ClaudeCodeAdapter().send("ping") == "PONG"
    assert fake.last_cmd[1:] == ["-p", "ping"]
    assert fake.last_kwargs["timeout"] == 120.0
    assert fake.last_kwargs["stdin"] == subprocess.DEVNULL


def test_claude_cleans_ansi_and_whitespace(monkeypatch):
    fake = _FakeRun(result=_completed(0, stdout="\x1b[32m  PONG \x1b[0m\n"))
    monkeypatch.setattr(RUN_TARGET, fake)
    assert ClaudeCodeAdapter().send("x") == "PONG"


def test_claude_nonzero_exit_raises_adaptererror(monkeypatch):
    fake = _FakeRun(result=_completed(2, stderr="\x1b[31mboom\x1b[0m\n"))
    monkeypatch.setattr(RUN_TARGET, fake)

    with pytest.raises(AdapterError) as exc_info:
        ClaudeCodeAdapter().send("x")

    message = str(exc_info.value)
    assert "claude exited with code 2" in message
    assert "boom" in message
    assert "\x1b" not in message  # stderr was cleaned


def test_claude_timeout_raises_adaptererror(monkeypatch):
    fake = _FakeRun(exc=subprocess.TimeoutExpired(cmd="claude", timeout=120.0))
    monkeypatch.setattr(RUN_TARGET, fake)
    with pytest.raises(AdapterError, match="timed out"):
        ClaudeCodeAdapter().send("x")


def test_claude_missing_executable_raises_adaptererror(monkeypatch):
    fake = _FakeRun(exc=FileNotFoundError())
    monkeypatch.setattr(RUN_TARGET, fake)
    with pytest.raises(AdapterError, match="not found"):
        ClaudeCodeAdapter().send("x")


# --- editor adapters (mocked) ---------------------------------------------


def test_vscode_builds_args_and_returns_stdout(monkeypatch):
    fake = _FakeRun(result=_completed(0, stdout="1.124.2\n"))
    monkeypatch.setattr(RUN_TARGET, fake)

    assert VSCodeAdapter().send("--version") == "1.124.2"
    assert fake.last_cmd[1:] == ["--version"]


def test_vscode_empty_stdout_returns_confirmation(monkeypatch):
    fake = _FakeRun(result=_completed(0, stdout=""))
    monkeypatch.setattr(RUN_TARGET, fake)
    assert VSCodeAdapter().send("--diff a.txt b.txt") == "code: ran --diff a.txt b.txt"


def test_vscode_nonzero_exit_raises(monkeypatch):
    fake = _FakeRun(result=_completed(1, stderr="bad flag"))
    monkeypatch.setattr(RUN_TARGET, fake)
    with pytest.raises(AdapterError, match="code exited with code 1"):
        VSCodeAdapter().send("--nope")


def test_vscode_timeout_raises(monkeypatch):
    fake = _FakeRun(exc=subprocess.TimeoutExpired(cmd="code", timeout=5))
    monkeypatch.setattr(RUN_TARGET, fake)
    with pytest.raises(AdapterError, match="timed out"):
        VSCodeAdapter().send("x")


def test_antigravity_builds_args_and_confirms(monkeypatch):
    fake = _FakeRun(result=_completed(0, stdout=""))
    monkeypatch.setattr(RUN_TARGET, fake)

    assert AntigravityEditorAdapter().send("somefile.py") == (
        "antigravity-ide: ran somefile.py"
    )
    assert fake.last_cmd[1:] == ["somefile.py"]


# --- defaults --------------------------------------------------------------


def test_default_executables_and_timeout():
    assert ClaudeCodeAdapter().executable_path.endswith("claude.exe")
    assert VSCodeAdapter().executable_path.endswith("code.cmd")
    assert AntigravityEditorAdapter().executable_path.endswith("antigravity-ide.cmd")
    assert ClaudeCodeAdapter().timeout == 120.0
