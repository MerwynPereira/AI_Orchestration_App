"""CLI entry point: ``python -m conductor <workflow.json> [--verbose] [--dry-run]``."""

from __future__ import annotations

import argparse
import logging
import sys

from .registry import ADAPTERS
from .runner import run_workflow
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
        run_workflow(workflow)
    except WorkflowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
