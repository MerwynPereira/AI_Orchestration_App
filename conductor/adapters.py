"""Adapter interface and concrete adapters.

An adapter is a uniform wrapper around "something that takes a prompt and
returns text" — an echo for testing, a CLI tool, or (later) a GUI-automated
chat window. Every adapter implements :meth:`Adapter.send`.
"""

from __future__ import annotations

import re
import subprocess
from abc import ABC, abstractmethod

# Claude Code is installed but not on PATH, so call it by absolute path.
DEFAULT_CLAUDE_PATH = r"C:\Users\merwy\.local\bin\claude.exe"

# Seconds to wait for `claude -p` before giving up on a hung process.
DEFAULT_TIMEOUT = 120.0

# Matches ANSI/VT100 escape sequences (CSI ... final byte). `claude -p` returns
# clean text today, but this is cheap insurance against TTY-dependent coloring.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _clean(text: str) -> str:
    """Strip ANSI escape sequences and surrounding whitespace from ``text``."""
    return _ANSI_RE.sub("", text).strip()


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


class ClaudeCodeAdapter(Adapter):
    """Runs ``claude -p "<prompt>"`` via subprocess and returns its stdout."""

    def __init__(
        self,
        claude_path: str = DEFAULT_CLAUDE_PATH,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Store the path to the ``claude`` executable and the call timeout.

        Args:
            claude_path: Absolute path to ``claude.exe``. Defaults to the
                known install location (Claude Code is not on PATH).
            timeout: Seconds to wait for the process before failing.
        """
        self._claude_path = claude_path
        self._timeout = timeout

    def send(self, prompt: str) -> str:
        """Run Claude Code in headless print mode and return its stdout.

        Args:
            prompt: The prompt to pass to ``claude -p``.

        Returns:
            The command's stdout, with ANSI escapes and surrounding
            whitespace stripped.

        Raises:
            AdapterError: If the executable is missing, exits non-zero, or
                does not finish within the timeout.
        """
        try:
            result = subprocess.run(
                [self._claude_path, "-p", prompt],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                # claude blocks ~3s waiting on stdin; redirect to skip it.
                stdin=subprocess.DEVNULL,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise AdapterError(
                f"claude executable not found at {self._claude_path!r}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(
                f"claude timed out after {self._timeout}s"
            ) from exc

        if result.returncode != 0:
            stderr = _clean(result.stderr or "")
            raise AdapterError(
                f"claude exited with code {result.returncode}: {stderr}"
            )

        return _clean(result.stdout or "")
