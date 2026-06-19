"""CLI entry point: ``python -m conductor <workflow.json> [options]``.

Options: ``--verbose``/``-v``, ``--dry-run``, and ``--output PATH`` (write a
structured JSON run log).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .registry import ADAPTERS
from .runner import STATUS_ERROR, STATUS_OK, StepResult, run_workflow
from .workflow import Workflow, WorkflowError, load_workflow


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="conductor",
        description="Run a Conductor workflow from a JSON file.",
    )
    parser.add_argument("workflow", help="Path to the workflow JSON file.")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging (shows each step's resolved prompt).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the step plan without executing any adapter.",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Write a structured JSON run log (per-step status/output/duration) "
        "to PATH after the workflow runs.",
    )
    return parser


def _configure_logging(verbose: bool) -> None:
    """Route runner logging to stdout as plain, human-readable lines."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )


def _print_plan(workflow: Workflow) -> None:
    """Print the resolved step plan for a dry run."""
    print(f"Plan for workflow: {workflow.name}")
    for index, step in enumerate(workflow.steps, start=1):
        suffix = f" (timeout={step.timeout}s)" if step.timeout is not None else ""
        id_label = f" [id={step.id}]" if step.id is not None else ""
        print(f"  {index}. {step.adapter}{id_label}{suffix}")
        print(f"     prompt: {step.prompt}")


def _build_run_log(workflow: Workflow, results: list[StepResult]) -> dict:
    """Build a JSON-serialisable run log from a completed run.

    Args:
        workflow: The workflow that ran (for its name).
        results: The per-step results returned by :func:`run_workflow`.

    Returns:
        A plain dict with the workflow name, an overall ``result`` (``"error"``
        if any step's status is an error, else ``"ok"``), and one entry per step
        with its index, id, adapter, status, output, error, and duration.
    """
    overall = (
        STATUS_ERROR
        if any(r.status == STATUS_ERROR for r in results)
        else STATUS_OK
    )
    return {
        "workflow": workflow.name,
        "result": overall,
        "steps": [
            {
                "index": r.index,
                "id": workflow.steps[r.index - 1].id,
                "adapter": r.adapter,
                "status": r.status,
                "output": r.output,
                "error": r.error,
                "duration_seconds": round(r.duration, 6),
            }
            for r in results
        ],
    }


def _write_run_log(path: str, workflow: Workflow, results: list[StepResult]) -> None:
    """Write the run log for ``results`` to ``path`` as pretty-printed JSON.

    Raises:
        OSError: If the file cannot be written (handled by the caller).
    """
    log = _build_run_log(workflow, results)
    Path(path).write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Load and run (or dry-run) a workflow named on the command line.

    Args:
        argv: Argument list excluding the program name. Defaults to
            ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 on success, 1 on a workflow error. (argparse exits
        with 2 on usage errors.)
    """
    args = _build_parser().parse_args(argv)
    _configure_logging(args.verbose)

    try:
        workflow = load_workflow(args.workflow, known_adapters=ADAPTERS)
    except WorkflowError as exc:
        print(f"Failed to load workflow: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        _print_plan(workflow)
        return 0

    try:
        results = run_workflow(workflow)
    except WorkflowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.output:
        try:
            _write_run_log(args.output, workflow, results)
        except OSError as exc:
            print(f"Failed to write run log to {args.output}: {exc}", file=sys.stderr)
            return 1
        print(f"Wrote run log to {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
