"""Pure display logic for the desktop UI.

Everything here is framework-agnostic: it takes plain engine objects
(:class:`~conductor.workflow.Workflow`, :class:`~conductor.workflow.Step`,
:class:`~conductor.runner.StepResult`, :class:`~conductor.workflow.WorkflowError`)
and returns strings or small frozen view dataclasses. No Flet (or any UI
framework) is imported, so this module is unit-tested directly while the Flet
shell in :mod:`conductor.ui.app` stays a thin, untested wiring layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from conductor.runner import STATUS_ERROR, StepResult
from conductor.workflow import Step, Workflow, WorkflowError


@dataclass(frozen=True)
class PlanRow:
    """A single read-only step row for the loaded-workflow plan.

    Attributes:
        headline: One-line summary — index, adapter, and (when present) id and
            timeout. Mirrors the CLI ``--dry-run`` plan line.
        prompt: The step's raw prompt template, shown beneath the headline.
    """

    headline: str
    prompt: str


@dataclass(frozen=True)
class ResultRow:
    """A single step outcome for the results view.

    Attributes:
        headline: One-line summary — index, adapter, status, and duration.
        output: The step's output text (may be empty).
        error: The failure message when the step errored, else ``None``.
        is_error: ``True`` when this step errored (for styling).
    """

    headline: str
    output: str
    error: str | None
    is_error: bool


def plan_title(workflow: Workflow) -> str:
    """Return the heading for a loaded workflow's plan.

    Args:
        workflow: The loaded workflow.

    Returns:
        e.g. ``"Workflow: demo — 3 steps"``.
    """
    return f"Workflow: {workflow.name} — {_count(len(workflow.steps), 'step')}"


def plan_row(index: int, step: Step) -> PlanRow:
    """Format one step into a :class:`PlanRow`.

    Args:
        index: 1-based step position.
        step: The step to describe.

    Returns:
        A :class:`PlanRow` whose headline carries the same fields as the CLI
        dry-run plan (index, adapter, optional id, optional timeout).
    """
    id_part = f"  [id={step.id}]" if step.id is not None else ""
    timeout_part = (
        f"  (timeout={step.timeout}s)" if step.timeout is not None else ""
    )
    headline = f"{index}. {step.adapter}{id_part}{timeout_part}"
    return PlanRow(headline=headline, prompt=step.prompt)


def plan_rows(workflow: Workflow) -> list[PlanRow]:
    """Format every step of ``workflow`` into :class:`PlanRow` objects."""
    return [
        plan_row(index, step)
        for index, step in enumerate(workflow.steps, start=1)
    ]


def result_row(result: StepResult) -> ResultRow:
    """Format one :class:`StepResult` into a :class:`ResultRow`.

    Args:
        result: The per-step outcome from :func:`conductor.runner.run_workflow`.

    Returns:
        A :class:`ResultRow` with a status/duration headline plus the output and
        any error.
    """
    is_error = result.status == STATUS_ERROR
    headline = (
        f"{result.index}. {result.adapter} — "
        f"{result.status} ({_seconds(result.duration)})"
    )
    return ResultRow(
        headline=headline,
        output=result.output,
        error=result.error,
        is_error=is_error,
    )


def result_rows(results: list[StepResult]) -> list[ResultRow]:
    """Format every :class:`StepResult` into a :class:`ResultRow`."""
    return [result_row(result) for result in results]


def overall_is_error(results: list[StepResult]) -> bool:
    """Return ``True`` if any step in ``results`` errored."""
    return any(result.status == STATUS_ERROR for result in results)


def overall_summary(results: list[StepResult]) -> str:
    """Return a one-line overall summary of a completed run.

    Args:
        results: The per-step outcomes.

    Returns:
        ``"Overall: ok — N steps completed"`` when every step succeeded, else
        ``"Overall: error — M of N steps failed"``.
    """
    total = len(results)
    failures = sum(1 for result in results if result.status == STATUS_ERROR)
    if failures == 0:
        return f"Overall: ok — {_count(total, 'step')} completed"
    return f"Overall: error — {failures} of {_count(total, 'step')} failed"


def format_load_error(error: WorkflowError) -> list[str]:
    """Split a load/validation error into one display line per problem.

    :func:`conductor.workflow.load_workflow` raises a single
    :class:`~conductor.workflow.WorkflowError` whose message joins every problem
    with ``"; "`` (the all-problems-at-once style). This re-splits that message
    so the UI can show one problem per line, without inventing any new text.

    Args:
        error: The error raised while loading a workflow.

    Returns:
        A non-empty list of problem strings (the engine's own wording).
    """
    return str(error).split("; ")


def _seconds(seconds: float) -> str:
    """Format a duration in seconds for display (e.g. ``"0.003s"``)."""
    return f"{seconds:.3f}s"


def _count(n: int, noun: str) -> str:
    """Return ``"1 step"`` / ``"3 steps"`` — naive English pluralization."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"
