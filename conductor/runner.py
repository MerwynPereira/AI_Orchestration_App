"""Workflow runner.

Executes a workflow's steps in order, feeding each step's output into the next
step's prompt via the ``{input}`` placeholder. Prints each step's output and
stops with a clear error if a step fails.
"""

from __future__ import annotations

from dataclasses import dataclass

from .adapters import Adapter, AdapterError, ClaudeCodeAdapter, EchoAdapter
from .workflow import Step, Workflow, WorkflowError

INPUT_PLACEHOLDER = "{input}"


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


def build_default_registry() -> dict[str, Adapter]:
    """Return the built-in adapter registry keyed by class name."""
    return {
        "EchoAdapter": EchoAdapter(),
        "ClaudeCodeAdapter": ClaudeCodeAdapter(),
    }


def _resolve_prompt(step: Step, previous_output: str) -> str:
    """Substitute the previous output into the step's prompt template.

    Args:
        step: The step whose prompt template to resolve.
        previous_output: Output of the prior step (empty for the first step).

    Returns:
        The prompt with ``{input}`` replaced.
    """
    return step.prompt.replace(INPUT_PLACEHOLDER, previous_output)


def _run_step(
    step: Step,
    index: int,
    previous_output: str,
    registry: dict[str, Adapter],
) -> StepResult:
    """Resolve and execute a single step.

    Args:
        step: The step to run.
        index: 1-based step position, for error messages.
        previous_output: Output of the prior step.
        registry: Adapter name -> instance.

    Returns:
        The :class:`StepResult`.

    Raises:
        WorkflowError: If the adapter is unknown or the adapter fails.
    """
    adapter = registry.get(step.adapter)
    if adapter is None:
        raise WorkflowError(f"step {index}: unknown adapter {step.adapter!r}")

    prompt = _resolve_prompt(step, previous_output)
    print(f"--- Step {index}: {step.adapter} ---")
    print(f"  prompt: {prompt}")

    try:
        output = adapter.send(prompt)
    except AdapterError as exc:
        raise WorkflowError(f"step {index} ({step.adapter}) failed: {exc}") from exc

    print(f"  output: {output}\n")
    return StepResult(index=index, adapter=step.adapter, output=output)


def run_workflow(
    workflow: Workflow,
    registry: dict[str, Adapter] | None = None,
) -> list[StepResult]:
    """Execute every step in order, chaining outputs into inputs.

    Args:
        workflow: The workflow to run.
        registry: Adapter name -> instance. Defaults to the built-in registry.

    Returns:
        One :class:`StepResult` per step, in order.

    Raises:
        WorkflowError: On the first step that fails; later steps do not run.
    """
    active_registry = registry if registry is not None else build_default_registry()
    results: list[StepResult] = []
    previous_output = ""

    for index, step in enumerate(workflow.steps, start=1):
        result = _run_step(step, index, previous_output, active_registry)
        previous_output = result.output
        results.append(result)

    return results
