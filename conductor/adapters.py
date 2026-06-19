"""Adapter interface and concrete adapters.

An adapter is a uniform wrapper around "something that takes a prompt and
returns text" — an echo for testing, a chat CLI, or an editor CLI. Every
adapter implements :meth:`Adapter.send`.

Two kinds of adapter live here:

* Chat-style adapters (e.g. :class:`ClaudeCodeAdapter`) where ``prompt`` is a
  natural-language prompt and the return value is the model's reply.
* Editor-style adapters (e.g. :class:`VSCodeAdapter`,
  :class:`AntigravityEditorAdapter`) where ``prompt`` is a string of
  command-line arguments for the editor's CLI (NOT a chat prompt). See those
  classes for the exact contract.

All CLI-backed adapters share :class:`CliAdapter`, which centralises the
subprocess hardening: absolute executable path, timeout, ``stdin`` redirected
away, and ``AdapterError`` on any failure.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from abc import ABC, abstractmethod

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
