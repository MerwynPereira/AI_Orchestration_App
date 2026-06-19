"""Tests for the pure UI display logic (``conductor.ui.presenter``).

These exercise only plain engine objects in / strings (and small frozen view
dataclasses) out. No Flet control is instantiated and no window is driven — the
Flet shell in ``conductor.ui.app`` is intentionally not unit-tested.
"""

from __future__ import annotations

from conductor.runner import STATUS_ERROR, STATUS_OK, StepResult
from conductor.ui import presenter
from conductor.workflow import Step, Workflow, WorkflowError


# --- plan view -------------------------------------------------------------


def test_plan_title_pluralizes_steps():
    one = Workflow(name="solo", steps=[Step(adapter="EchoAdapter", prompt="hi")])
    many = Workflow(
        name="demo",
        steps=[
            Step(adapter="EchoAdapter", prompt="a"),
            Step(adapter="EchoAdapter", prompt="b"),
        ],
    )
    assert presenter.plan_title(one) == "Workflow: solo — 1 step"
    assert presenter.plan_title(many) == "Workflow: demo — 2 steps"


def test_plan_row_minimal_step():
    row = presenter.plan_row(1, Step(adapter="EchoAdapter", prompt="hello"))
    assert row.headline == "1. EchoAdapter"
    assert row.prompt == "hello"


def test_plan_row_includes_id_and_timeout():
    step = Step(adapter="ClaudeCodeAdapter", prompt="go", id="draft", timeout=60.0)
    row = presenter.plan_row(2, step)
    # Same fields the CLI --dry-run plan shows: index, adapter, id, timeout.
    assert row.headline == "2. ClaudeCodeAdapter  [id=draft]  (timeout=60.0s)"
    assert row.prompt == "go"


def test_plan_rows_covers_every_step_in_order():
    workflow = Workflow(
        name="demo",
        steps=[
            Step(adapter="EchoAdapter", prompt="one", id="a"),
            Step(adapter="EchoAdapter", prompt="two"),
        ],
    )
    rows = presenter.plan_rows(workflow)
    assert [r.headline for r in rows] == ["1. EchoAdapter  [id=a]", "2. EchoAdapter"]
    assert [r.prompt for r in rows] == ["one", "two"]


# --- result view -----------------------------------------------------------


def test_result_row_ok():
    result = StepResult(index=1, adapter="EchoAdapter", output="hi", duration=0.0123)
    row = presenter.result_row(result)
    assert row.headline == "1. EchoAdapter — ok (0.012s)"
    assert row.output == "hi"
    assert row.error is None
    assert row.is_error is False


def test_result_row_error_flags_and_carries_message():
    result = StepResult(
        index=3,
        adapter="BoomAdapter",
        output="",
        status=STATUS_ERROR,
        error="step 3 (BoomAdapter) failed: kaboom",
        duration=1.5,
    )
    row = presenter.result_row(result)
    assert row.headline == "3. BoomAdapter — error (1.500s)"
    assert row.is_error is True
    assert row.error == "step 3 (BoomAdapter) failed: kaboom"
    assert row.output == ""


def test_result_rows_preserves_order():
    results = [
        StepResult(index=1, adapter="EchoAdapter", output="a", duration=0.0),
        StepResult(index=2, adapter="EchoAdapter", output="b", duration=0.0),
    ]
    rows = presenter.result_rows(results)
    assert [r.headline.split(".")[0] for r in rows] == ["1", "2"]


# --- overall summary -------------------------------------------------------


def test_overall_summary_all_ok():
    results = [
        StepResult(index=1, adapter="EchoAdapter", output="a", duration=0.0),
        StepResult(index=2, adapter="EchoAdapter", output="b", duration=0.0),
    ]
    assert presenter.overall_is_error(results) is False
    assert presenter.overall_summary(results) == "Overall: ok — 2 steps completed"


def test_overall_summary_with_a_failure():
    results = [
        StepResult(index=1, adapter="EchoAdapter", output="a", status=STATUS_OK, duration=0.0),
        StepResult(
            index=2,
            adapter="BoomAdapter",
            output="",
            status=STATUS_ERROR,
            error="boom",
            duration=0.0,
        ),
    ]
    assert presenter.overall_is_error(results) is True
    assert presenter.overall_summary(results) == "Overall: error — 1 of 2 steps failed"


def test_overall_summary_single_step_is_singular():
    results = [StepResult(index=1, adapter="EchoAdapter", output="x", duration=0.0)]
    assert presenter.overall_summary(results) == "Overall: ok — 1 step completed"


# --- load errors -----------------------------------------------------------


def test_format_load_error_splits_problems_into_lines():
    exc = WorkflowError("wf.json: unknown adapter 'Nope'; 'timeout' must be a positive number")
    assert presenter.format_load_error(exc) == [
        "wf.json: unknown adapter 'Nope'",
        "'timeout' must be a positive number",
    ]


def test_format_load_error_single_problem_is_one_line():
    exc = WorkflowError("workflow file not found: missing.json")
    assert presenter.format_load_error(exc) == ["workflow file not found: missing.json"]
