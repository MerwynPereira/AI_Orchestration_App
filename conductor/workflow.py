"""Workflow model, validation, and JSON loading.

A workflow is an ordered list of steps. Each step names an adapter and carries
a prompt template (and an optional per-step timeout). The runner executes the
steps in order.

Validation (:func:`validate_workflow`) reports *all* problems at once rather
than stopping at the first, so a single run surfaces every issue in a file.
"""

from __future__ import annotations

import json
from collections.abc import Container
from dataclasses import dataclass
from pathlib import Path


class WorkflowError(Exception):
    """Raised when a workflow file is missing, malformed, or invalid."""


@dataclass(frozen=True)
class Step:
    """A single workflow step.

    Attributes:
        adapter: Name of the adapter to run (e.g. ``"EchoAdapter"``).
        prompt: Prompt template; may contain a ``{input}`` placeholder that the
            runner replaces with the previous step's output.
        timeout: Optional per-step timeout in seconds. When set, it overrides
            the adapter's default timeout (only meaningful for CLI adapters).
    """

    adapter: str
    prompt: str
    timeout: float | None = None


@dataclass(frozen=True)
class Workflow:
    """A named, ordered sequence of steps.

    Attributes:
        name: Human-readable workflow name.
        steps: Steps to execute in order.
    """

    name: str
    steps: list[Step]


def validate_workflow(
    data: object,
    known_adapters: Container[str] | None = None,
) -> list[str]:
    """Return every validation problem found in decoded workflow ``data``.

    This does no I/O; pass it the object produced by :func:`json.loads`. It
    accumulates all problems instead of raising on the first one.

    Args:
        data: The decoded JSON value to validate.
        known_adapters: If given, each step's adapter must be a member, else an
            "unknown adapter" problem is reported. If ``None``, adapter names
            are not checked (the runner still guards against unknown adapters).

    Returns:
        A list of human-readable problem descriptions (empty if valid).
    """
    if not isinstance(data, dict):
        return ["top level must be a JSON object"]

    problems: list[str] = []

    name = data.get("name")
    if not isinstance(name, str) or not name:
        problems.append("'name' must be a non-empty string")

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list):
        problems.append("'steps' must be a list")
    elif not raw_steps:
        problems.append("'steps' must not be empty")
    else:
        for index, item in enumerate(raw_steps, start=1):
            problems.extend(_validate_step(item, index, known_adapters))

    return problems


def _validate_step(
    item: object,
    index: int,
    known_adapters: Container[str] | None,
) -> list[str]:
    """Return all validation problems for a single raw step.

    Args:
        item: One element of the workflow's ``steps`` list.
        index: 1-based step position, for messages.
        known_adapters: Known adapter names, or ``None`` to skip the check.

    Returns:
        A list of problem descriptions for this step (empty if valid).
    """
    prefix = f"step {index}"
    if not isinstance(item, dict):
        return [f"{prefix} must be a JSON object"]

    problems: list[str] = []

    adapter = item.get("adapter")
    if not isinstance(adapter, str) or not adapter:
        problems.append(f"{prefix}: 'adapter' must be a non-empty string")
    elif known_adapters is not None and adapter not in known_adapters:
        problems.append(f"{prefix}: unknown adapter {adapter!r}")

    if not isinstance(item.get("prompt"), str):
        problems.append(f"{prefix}: 'prompt' must be a string")

    timeout = item.get("timeout")
    if timeout is not None and not _is_positive_number(timeout):
        problems.append(f"{prefix}: 'timeout' must be a positive number")

    return problems


def _is_positive_number(value: object) -> bool:
    """Return True if ``value`` is a positive int/float (but not a bool)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def load_workflow(
    path: Path | str,
    known_adapters: Container[str] | None = None,
) -> Workflow:
    """Load, validate, and build a workflow from a JSON file.

    Args:
        path: Path to the workflow JSON file.
        known_adapters: Passed through to :func:`validate_workflow` so unknown
            adapter names are caught at load time.

    Returns:
        The parsed :class:`Workflow`.

    Raises:
        WorkflowError: If the file is missing/unreadable, is not valid JSON, or
            fails validation. Validation errors list every problem found.
    """
    workflow_path = Path(path)

    try:
        raw = workflow_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WorkflowError(f"workflow file not found: {workflow_path}") from exc
    except OSError as exc:
        raise WorkflowError(f"could not read {workflow_path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"invalid JSON in {workflow_path}: {exc}") from exc

    problems = validate_workflow(data, known_adapters)
    if problems:
        raise WorkflowError(f"{workflow_path}: {'; '.join(problems)}")

    return _build_workflow(data)


def _build_workflow(data: dict) -> Workflow:
    """Build a :class:`Workflow` from already-validated ``data``."""
    steps = [_build_step(item) for item in data["steps"]]
    return Workflow(name=data["name"], steps=steps)


def _build_step(item: dict) -> Step:
    """Build a :class:`Step` from an already-validated raw step."""
    timeout = item.get("timeout")
    return Step(
        adapter=item["adapter"],
        prompt=item["prompt"],
        timeout=float(timeout) if timeout is not None else None,
    )
