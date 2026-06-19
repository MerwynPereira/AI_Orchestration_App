"""Workflow runner.

Executes a workflow's steps in order, feeding each step's output into the next
step's prompt. A prompt may reference the immediately-previous output via
``{input}`` and any earlier step's output by id via ``{steps.<id>}``. Step
progress is reported through the stdlib :mod:`logging` module (module-level
``logger``); execution stops with a clear
:class:`~conductor.workflow.WorkflowError` if a step fails, naming the step that
failed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .adapters import Adapter, AdapterError, CliAdapter
from .registry import ADAPTERS, create_adapter
from .workflow import Step, Workflow, WorkflowError

logger = logging.getLogger(__name__)

INPUT_PLACEHOLDER = "{input}"

#: Single-pass matcher for both placeholder kinds: ``{input}`` (group 0, no
#: capture) and ``{steps.<id>}`` (group 1 is the id). Matching both in one
#: ``re.sub`` pass means substituted text is never re-scanned, so an output that
#: happens to contain ``{input}`` or ``{steps.x}`` is inserted literally.
_PLACEHOLDER_RE = re.compile(r"\{input\}|\{steps\.([A-Za-z0-9_-]+)\}")


@dataclass(frozen=True)
class StepResult:
    """The outcome of one executed step.

    Attributes:
        index: 1-based step position.
        adapter: Name of the adapter that ran.
        output: The adapter's response.
    """

    index: int
    adapter: str
    output: str


def _resolve_prompt(
    step: Step,
    previous_output: str,
    outputs: dict[str, str] | None = None,
) -> str:
    """Substitute placeholders in the step's prompt template.

    Replaces ``{input}`` with the previous step's output and each
    ``{steps.<id>}`` with that earlier step's recorded output, in a single pass
    so substituted text is never re-scanned. Unknown ids resolve to ``""``
    (validation rejects them at load time; this is only the runtime fallback).

    Args:
        step: The step whose prompt template to resolve.
        previous_output: Output of the prior step (empty for the first step).
        outputs: Map of step id -> that step's output, for ``{steps.<id>}``
            references. Defaults to empty.

    Returns:
        The prompt with both placeholder kinds replaced.
    """
    by_id = outputs or {}

    def _replace(match: re.Match[str]) -> str:
        ref_id = match.group(1)
        if ref_id is None:  # matched {input}
            return previous_output
        return by_id.get(ref_id, "")

    return _PLACEHOLDER_RE.sub(_replace, step.prompt)


def _run_step(
    step: Step,
    index: int,
    previous_output: str,
    outputs: dict[str, str],
    registry: dict[str, type[Adapter]],
) -> StepResult:
    """Resolve and execute a single step.

    Args:
        step: The step to run.
        index: 1-based step position, for messages.
        previous_output: Output of the prior step.
        outputs: Map of step id -> output, for ``{steps.<id>}`` references.
        registry: Adapter name -> adapter class.

    Returns:
        The :class:`StepResult`.

    Raises:
        WorkflowError: If the adapter is unknown or the adapter fails.
    """
    try:
        adapter = create_adapter(step.adapter, registry)
    except AdapterError as exc:
        raise WorkflowError(f"step {index}: {exc}") from exc

    if step.timeout is not None and isinstance(adapter, CliAdapter):
        adapter.timeout = step.timeout

    prompt = _resolve_prompt(step, previous_output, outputs)
    logger.info("Step %d: %s", index, step.adapter)
    logger.debug("  prompt: %s", prompt)

    try:
        output = adapter.send(prompt)
    except AdapterError as exc:
        raise WorkflowError(f"step {index} ({step.adapter}) failed: {exc}") from exc

    logger.info("  output: %s", output)
    return StepResult(index=index, adapter=step.adapter, output=output)


def run_workflow(
    workflow: Workflow,
    registry: dict[str, type[Adapter]] | None = None,
) -> list[StepResult]:
    """Execute every step in order, chaining outputs into inputs.

    Args:
        workflow: The workflow to run.
        registry: Adapter name -> adapter class. Defaults to the built-in
            :data:`conductor.registry.ADAPTERS`.

    Returns:
        One :class:`StepResult` per step, in order.

    Raises:
        WorkflowError: On the first step that fails; later steps do not run.
    """
    active_registry = registry if registry is not None else ADAPTERS
    results: list[StepResult] = []
    outputs: dict[str, str] = {}
    previous_output = ""

    logger.info("Running workflow: %s", workflow.name)
    for index, step in enumerate(workflow.steps, start=1):
        result = _run_step(step, index, previous_output, outputs, active_registry)
        previous_output = result.output
        if step.id is not None:
            outputs[step.id] = result.output
        results.append(result)

    logger.info("Workflow complete (%d steps).", len(results))
    return results
