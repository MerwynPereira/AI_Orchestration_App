"""Workflow model and JSON loading.

A workflow is an ordered list of steps. Each step names an adapter and carries
a prompt template. The runner executes them in order.
"""

from __future__ import annotations

import json
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
    """

    adapter: str
    prompt: str


@dataclass(frozen=True)
class Workflow:
    """A named, ordered sequence of steps.

    Attributes:
        name: Human-readable workflow name.
        steps: Steps to execute in order.
    """

    name: str
    steps: list[Step]


def load_workflow(path: Path | str) -> Workflow:
    """Load and validate a workflow from a JSON file.

    Args:
        path: Path to the workflow JSON file.

    Returns:
        The parsed :class:`Workflow`.

    Raises:
        WorkflowError: If the file is missing, not valid JSON, or missing
            required fields.
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

    return _parse_workflow(data, workflow_path)


def _parse_workflow(data: object, source: Path) -> Workflow:
    """Validate a decoded JSON object and build a :class:`Workflow`.

    Args:
        data: The object produced by :func:`json.loads`.
        source: Path used only for error messages.

    Returns:
        The validated :class:`Workflow`.

    Raises:
        WorkflowError: If the shape or types are wrong.
    """
    if not isinstance(data, dict):
        raise WorkflowError(f"{source}: top level must be a JSON object")

    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise WorkflowError(f"{source}: 'name' must be a non-empty string")

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise WorkflowError(f"{source}: 'steps' must be a non-empty list")

    steps = [_parse_step(item, index, source) for index, item in enumerate(raw_steps, start=1)]
    return Workflow(name=name, steps=steps)


def _parse_step(item: object, index: int, source: Path) -> Step:
    """Validate a single raw step object and build a :class:`Step`.

    Args:
        item: One element of the workflow's ``steps`` list.
        index: 1-based step position, for error messages.
        source: Path used only for error messages.

    Returns:
        The validated :class:`Step`.

    Raises:
        WorkflowError: If required fields are missing or mistyped.
    """
    if not isinstance(item, dict):
        raise WorkflowError(f"{source}: step {index} must be a JSON object")

    adapter = item.get("adapter")
    if not isinstance(adapter, str) or not adapter:
        raise WorkflowError(f"{source}: step {index} 'adapter' must be a non-empty string")

    prompt = item.get("prompt")
    if not isinstance(prompt, str):
        raise WorkflowError(f"{source}: step {index} 'prompt' must be a string")

    return Step(adapter=adapter, prompt=prompt)
