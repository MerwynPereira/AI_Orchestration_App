"""CLI entry point: ``python -m conductor <workflow.json>``."""

from __future__ import annotations

import sys
from pathlib import Path

from .runner import run_workflow
from .workflow import WorkflowError, load_workflow

USAGE = "Usage: python -m conductor <workflow.json>"


def main(argv: list[str] | None = None) -> int:
    """Load and run a workflow named on the command line.

    Args:
        argv: Argument list excluding the program name. Defaults to
            ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 on success, 1 on a workflow error, 2 on misuse.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(USAGE, file=sys.stderr)
        return 2

    try:
        workflow = load_workflow(Path(args[0]))
    except WorkflowError as exc:
        print(f"Failed to load workflow: {exc}", file=sys.stderr)
        return 1

    print(f"Running workflow: {workflow.name}\n")

    try:
        run_workflow(workflow)
    except WorkflowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("Workflow complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
