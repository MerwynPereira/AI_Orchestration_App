"""Adapter interface and concrete adapters.

An adapter is a uniform wrapper around "something that takes a prompt and
returns text" — an echo for testing, a chat CLI, or an editor CLI. Every
adapter implements :meth:`Adapter.send`.

Three kinds of adapter live here:

* Chat-style adapters (e.g. :class:`ClaudeCodeAdapter`) where ``prompt`` is a
  natural-language prompt and the return value is the model's reply.
* Editor-style adapters (e.g. :class:`VSCodeAdapter`,
  :class:`AntigravityEditorAdapter`) where ``prompt`` is a string of
  command-line arguments for the editor's CLI (NOT a chat prompt). See those
  classes for the exact contract.
* GUI-chat adapters (e.g. :class:`ClaudeDesktopAdapter`) where ``prompt`` is a
  natural-language prompt driven into a desktop chat window via window focus +
  clipboard, and the response is read back by polling until the text settles.

All CLI-backed adapters share :class:`CliAdapter`, which centralises the
subprocess hardening: absolute executable path, timeout, ``stdin`` redirected
away, and ``AdapterError`` on any failure. GUI-chat adapters share
:class:`GuiChatAdapter`, which centralises the focus → submit → poll loop and
its hard overall timeout.
"""

from __future__ import annotations

import importlib
import re
import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Callable

# Claude Code is installed but not on PATH, so call it by absolute path.
DEFAULT_CLAUDE_PATH = r"C:\Users\merwy\.local\bin\claude.exe"

# VS Code and Antigravity ship `.cmd` launchers in their `bin` directories.
# subprocess runs these directly (shell=False) on this machine.
DEFAULT_VSCODE_PATH = (
    r"C:\Users\merwy\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd"
)
DEFAULT_ANTIGRAVITY_PATH = (
    r"C:\Users\merwy\AppData\Local\Programs\Antigravity IDE\bin\antigravity-ide.cmd"
)

# Seconds to wait for a CLI before giving up on a hung process.
DEFAULT_TIMEOUT = 120.0

# GUI-chat tuning. The hard wall is DEFAULT_GUI_TIMEOUT; a response is judged
# complete once its text stops changing for DEFAULT_STABLE_FOR seconds, sampled
# every DEFAULT_POLL_INTERVAL seconds.
DEFAULT_GUI_TIMEOUT = 120.0
DEFAULT_STABLE_FOR = 2.0
DEFAULT_POLL_INTERVAL = 0.5

# Claude Desktop is an Electron app; its top-level window title starts "Claude".
DEFAULT_CLAUDE_DESKTOP_TITLE_RE = r"^Claude"

# Matches ANSI/VT100 escape sequences (CSI ... final byte). The CLIs return
# clean text today, but this is cheap insurance against TTY-dependent coloring.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _clean(text: str) -> str:
    """Strip ANSI escape sequences and surrounding whitespace from ``text``."""
    return _ANSI_RE.sub("", text).strip()


def _split_args(arg_string: str) -> list[str]:
    """Split a CLI argument string into tokens, Windows-path-aware.

    Uses :func:`shlex.split` in non-POSIX mode so backslashes in Windows paths
    are preserved, then strips one pair of surrounding quotes from each token so
    quoted paths containing spaces survive as a single argument.

    Args:
        arg_string: The raw argument string (an adapter ``prompt``).

    Returns:
        The parsed argument tokens (empty list for a blank string).
    """
    tokens = shlex.split(arg_string, posix=False)
    cleaned: list[str] = []
    for token in tokens:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
            token = token[1:-1]
        cleaned.append(token)
    return cleaned


def _import_module(name: str, tool_name: str):
    """Import an optional dependency, raising ``AdapterError`` if it is missing.

    GUI-chat adapters depend on third-party packages (pywinauto, pyperclip) that
    the core engine does not. Importing them lazily keeps the package importable
    everywhere; this helper turns a missing dependency into the adapter contract's
    ``AdapterError`` instead of a bare ``ImportError``.

    Args:
        name: The module to import (e.g. ``"pywinauto"``).
        tool_name: Adapter tool name, used in the error message.

    Returns:
        The imported module.

    Raises:
        AdapterError: If the module cannot be imported.
    """
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise AdapterError(
            f"{tool_name} needs the {name!r} package; install requirements.txt"
        ) from exc


def _poll_until_stable(
    read: Callable[[], str],
    *,
    overall_timeout: float,
    stable_for: float,
    poll_interval: float,
    now: Callable[[], float],
    sleep: Callable[[float], None],
    tool_name: str = "GUI chat",
) -> str:
    """Poll ``read`` until its text stops changing, then return that text.

    This is the response-complete heuristic: a streaming reply keeps growing, so
    once ``read()`` returns the same non-empty text for ``stable_for`` seconds it
    is treated as finished. ``now``/``sleep`` are injected so tests can drive a
    fake clock deterministically.

    Args:
        read: Callable returning the current response text (``""`` until the
            reply starts).
        overall_timeout: Hard wall, in seconds, across the whole poll.
        stable_for: Seconds the text must stay unchanged to count as complete.
        poll_interval: Seconds to wait between samples.
        now: Monotonic time source (returns seconds).
        sleep: Sleep function (takes seconds).
        tool_name: Adapter tool name, used in the timeout message.

    Returns:
        The stabilised, non-empty response text.

    Raises:
        AdapterError: If no stable, non-empty response appears before
            ``overall_timeout`` elapses.
    """
    deadline = now() + overall_timeout
    last_text: str | None = None
    stable_since: float | None = None
    while True:
        current = read()
        timestamp = now()
        if current != last_text:
            # Still changing (or first sample): reset the stability clock.
            last_text = current
            stable_since = timestamp
        elif current and stable_since is not None and timestamp - stable_since >= stable_for:
            return current
        if timestamp >= deadline:
            raise AdapterError(
                f"{tool_name} did not finish responding within {overall_timeout}s"
            )
        sleep(poll_interval)


class AdapterError(Exception):
    """Raised when an adapter fails to produce a response."""


class Adapter(ABC):
    """Abstract base for anything that turns a prompt into a text response."""

    @abstractmethod
    def send(self, prompt: str) -> str:
        """Send ``prompt`` and return the response text.

        Args:
            prompt: The fully-resolved prompt to send (placeholders already
                substituted by the runner).

        Returns:
            The adapter's response as a string.

        Raises:
            AdapterError: If the adapter cannot produce a response.
        """
        raise NotImplementedError


class EchoAdapter(Adapter):
    """Returns the prompt unchanged — lets you test a chain with no tool."""

    def send(self, prompt: str) -> str:
        """Return ``prompt`` verbatim."""
        return prompt


class CliAdapter(Adapter):
    """Base for adapters that shell out to a command-line tool.

    Subclasses set :attr:`tool_name` (used in error messages) and
    :attr:`default_executable`, and implement :meth:`_build_args` to turn a
    prompt into CLI arguments. Subclasses may override :meth:`_format_output`
    to shape the returned text.

    The ``timeout`` attribute is public and mutable so the runner can apply a
    per-step override before calling :meth:`send`.
    """

    #: Short tool name used in error messages (e.g. ``"claude"``).
    tool_name: str = "command"
    #: Default absolute path to the executable.
    default_executable: str = ""

    def __init__(
        self,
        executable_path: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Store the executable path and the call timeout.

        Args:
            executable_path: Path to the executable. Defaults to
                :attr:`default_executable`.
            timeout: Seconds to wait for the process before failing.
        """
        self.executable_path = executable_path or self.default_executable
        self.timeout = timeout

    def _build_args(self, prompt: str) -> list[str]:
        """Return the arguments (after the executable) for ``prompt``."""
        raise NotImplementedError

    def _format_output(self, output: str, args: list[str]) -> str:
        """Shape the cleaned stdout before returning it. Identity by default."""
        return output

    def send(self, prompt: str) -> str:
        """Run the tool and return its (cleaned, formatted) stdout.

        Raises:
            AdapterError: If the executable is missing, exits non-zero, or does
                not finish within :attr:`timeout`.
        """
        args = [self.executable_path, *self._build_args(prompt)]
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                # Some CLIs block waiting on stdin; redirect to skip it.
                stdin=subprocess.DEVNULL,
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise AdapterError(
                f"{self.tool_name} executable not found at {self.executable_path!r}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(
                f"{self.tool_name} timed out after {self.timeout}s"
            ) from exc

        if result.returncode != 0:
            stderr = _clean(result.stderr or "")
            raise AdapterError(
                f"{self.tool_name} exited with code {result.returncode}: {stderr}"
            )

        return self._format_output(_clean(result.stdout or ""), args)


class ClaudeCodeAdapter(CliAdapter):
    """Runs ``claude -p "<prompt>"`` via subprocess and returns its stdout.

    Chat-style contract: ``prompt`` is a natural-language prompt; the return
    value is Claude Code's reply.
    """

    tool_name = "claude"
    default_executable = DEFAULT_CLAUDE_PATH

    def _build_args(self, prompt: str) -> list[str]:
        """Run Claude Code in headless print mode."""
        return ["-p", prompt]


class VSCodeAdapter(CliAdapter):
    """Wraps the VS Code ``code`` CLI for editor operations.

    Editor-style contract: ``prompt`` is a string of ``code`` CLI arguments,
    NOT a chat prompt and NOT an AI feature. Examples:

    * ``"path/to/file.py"`` — open a file.
    * ``"--diff a.txt b.txt"`` — open a diff view.
    * ``"--version"`` — print version info.

    Returns the command's stdout, or a short confirmation when stdout is empty
    (most editor commands print nothing). Do NOT pass ``--wait``: it blocks
    until the editor window is closed, which would hang until the timeout.
    """

    tool_name = "code"
    default_executable = DEFAULT_VSCODE_PATH

    def _build_args(self, prompt: str) -> list[str]:
        return _split_args(prompt)

    def _format_output(self, output: str, args: list[str]) -> str:
        if output:
            return output
        return f"{self.tool_name}: ran {' '.join(args[1:])}".rstrip()


class AntigravityEditorAdapter(CliAdapter):
    """Wraps the ``antigravity-ide`` editor CLI for editor operations.

    Editor-style contract: identical to :class:`VSCodeAdapter` — ``prompt`` is
    a string of ``antigravity-ide`` CLI arguments for editor actions only. This
    deliberately does NOT touch Antigravity's AI agent. Avoid ``--wait``.
    """

    tool_name = "antigravity-ide"
    default_executable = DEFAULT_ANTIGRAVITY_PATH

    def _build_args(self, prompt: str) -> list[str]:
        return _split_args(prompt)

    def _format_output(self, output: str, args: list[str]) -> str:
        if output:
            return output
        return f"{self.tool_name}: ran {' '.join(args[1:])}".rstrip()


class GuiChatAdapter(Adapter):
    """Base for chat-only desktop tools driven via window focus + clipboard.

    Chat-style contract: ``prompt`` is a natural-language prompt; the return
    value is the assistant's reply. Unlike :class:`CliAdapter`, there is no
    process to capture — the adapter focuses a desktop window, pastes the prompt,
    submits it, then reads the reply back by polling until the text settles (see
    :func:`_poll_until_stable`).

    Subclasses provide the three physical actions, each of which must raise
    ``AdapterError`` (never a raw pywinauto/OS error) on failure:

    * :meth:`_focus_window` — bring the target window to the foreground.
    * :meth:`_submit_prompt` — put ``prompt`` into the message box and send it.
    * :meth:`_read_response` — return the current response text (``""`` until it
      starts), called repeatedly while polling.

    Those actions are the seam the tests mock, mirroring how :class:`CliAdapter`
    tests mock ``subprocess.run`` — no test drives a real window.

    The timing attributes (``overall_timeout``, ``stable_for``,
    ``poll_interval``) are public and mutable so a caller can tune them; ``_now``
    and ``_sleep`` are injectable so tests can run a fake clock.
    """

    #: Short tool name used in error messages (e.g. ``"Claude Desktop"``).
    tool_name: str = "gui-chat"

    def __init__(
        self,
        *,
        overall_timeout: float = DEFAULT_GUI_TIMEOUT,
        stable_for: float = DEFAULT_STABLE_FOR,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        """Store the polling configuration.

        Args:
            overall_timeout: Hard wall, in seconds, for the response wait.
            stable_for: Seconds the reply text must stay unchanged to be judged
                complete.
            poll_interval: Seconds between response samples.
        """
        self.overall_timeout = overall_timeout
        self.stable_for = stable_for
        self.poll_interval = poll_interval
        # Injectable so tests can drive a deterministic fake clock.
        self._now: Callable[[], float] = time.monotonic
        self._sleep: Callable[[float], None] = time.sleep

    def _focus_window(self) -> None:
        """Bring the target window to the foreground (subclass responsibility)."""
        raise NotImplementedError

    def _submit_prompt(self, prompt: str) -> None:
        """Type/paste ``prompt`` into the window and submit it."""
        raise NotImplementedError

    def _read_response(self) -> str:
        """Return the current response text (``""`` until the reply begins)."""
        raise NotImplementedError

    def send(self, prompt: str) -> str:
        """Drive ``prompt`` through the desktop window and return the reply.

        Raises:
            AdapterError: If the prompt is empty, the window cannot be focused or
                submitted to, or no stable response arrives within
                :attr:`overall_timeout`.
        """
        if not prompt.strip():
            raise AdapterError(f"{self.tool_name}: prompt is empty")
        try:
            self._focus_window()
            self._submit_prompt(prompt)
        except AdapterError:
            raise
        except Exception as exc:  # never let a raw pywinauto/OS error escape
            raise AdapterError(f"{self.tool_name}: automation failed: {exc}") from exc
        return self._await_response()

    def _await_response(self) -> str:
        """Poll :meth:`_read_response` until the reply settles or times out."""
        try:
            return _poll_until_stable(
                self._read_response,
                overall_timeout=self.overall_timeout,
                stable_for=self.stable_for,
                poll_interval=self.poll_interval,
                now=self._now,
                sleep=self._sleep,
                tool_name=self.tool_name,
            )
        except AdapterError:
            raise
        except Exception as exc:  # a raw error from _read_response
            raise AdapterError(
                f"{self.tool_name}: failed to read response: {exc}"
            ) from exc


class ClaudeDesktopAdapter(GuiChatAdapter):
    """Drives the Claude Desktop window via pywinauto (UIA) + pyperclip.

    SPIKE: this proves the focus → paste → poll loop is structured correctly.
    The detection logic and every error path are covered by tests with the
    automation layer mocked; the real run against a live Claude Desktop window
    has NOT been validated here and is the open question this spike exists to
    answer.

    Known fragile point (the spike's whole reason to exist): reading *only* the
    latest assistant reply out of the conversation is app-specific and the most
    likely thing to need iteration. :meth:`_read_response` is a deliberately
    thin, best-effort implementation; harden it once the loop proves viable.

    Requires the user to already be signed in. It never automates credentials,
    never bypasses any rate/usage limit, and is paced by :attr:`poll_interval`.
    """

    tool_name = "Claude Desktop"
    #: Regex matched against the top-level window title.
    window_title_re: str = DEFAULT_CLAUDE_DESKTOP_TITLE_RE

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # Gap between paste and Enter so the UI registers the pasted text.
        self.submit_delay = 0.3
        self._window = None

    def _focus_window(self) -> None:
        pywinauto = _import_module("pywinauto", self.tool_name)
        from pywinauto.findwindows import ElementNotFoundError

        try:
            window = pywinauto.Desktop(backend="uia").window(
                title_re=self.window_title_re
            )
            window.set_focus()
            self._window = window
        except ElementNotFoundError as exc:
            raise AdapterError(
                f"{self.tool_name} window not found "
                f"(title matching {self.window_title_re!r}); is it running?"
            ) from exc

    def _submit_prompt(self, prompt: str) -> None:
        pyperclip = _import_module("pyperclip", self.tool_name)
        from pywinauto.keyboard import send_keys

        pyperclip.copy(prompt)
        send_keys("^v")  # paste into the focused message box
        self._sleep(self.submit_delay)
        send_keys("{ENTER}")  # submit

    def _read_response(self) -> str:
        """Best-effort read of the latest reply text from the window tree.

        Reads the conversation's text directly from the UIA tree rather than the
        clipboard, so sampling has no side effects and a stale clipboard cannot
        masquerade as a finished reply. Returns ``""`` while the reply has not
        appeared yet. Pinning this to *only* the last assistant turn is the part
        most likely to need hardening after the spike.
        """
        if self._window is None:
            return ""
        texts = self._window.descendants(control_type="Text")
        if not texts:
            return ""
        return _clean(texts[-1].window_text() or "")
