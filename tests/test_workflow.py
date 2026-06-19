"""Tests for the workflow model, validation, and JSON loading."""

from __future__ import annotations

import json

import pytest

from conductor.workflow import (
    Step,
    WorkflowError,
    load_workflow,
    validate_workflow,
)

KNOWN = {"EchoAdapter", "ClaudeCodeAdapter"}


def _write(tmp_path, data, name="wf.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- loading ---------------------------------------------------------------


def test_load_valid_workflow(tmp_path):
    path = _write(
        tmp_path,
        {"name": "demo", "steps": [{"adapter": "EchoAdapter", "prompt": "hi"}]},
    )
    workflow = load_workflow(path, known_adapters=KNOWN)
    assert workflow.name == "demo"
    assert workflow.steps == [Step(adapter="EchoAdapter", prompt="hi", timeout=None)]


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(WorkflowError, match="not found"):
        load_workflow(tmp_path / "nope.json")


def test_load_bad_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(WorkflowError, match="invalid JSON"):
        load_workflow(path)


def test_load_parses_timeout_as_float(tmp_path):
    path = _write(
        tmp_path,
        {
            "name": "d",
            "steps": [{"adapter": "EchoAdapter", "prompt": "x", "timeout": 15}],
        },
    )
    step = load_workflow(path, known_adapters=KNOWN).steps[0]
    assert step.timeout == 15.0
    assert isinstance(step.timeout, float)


def test_load_raises_listing_all_problems(tmp_path):
    path = _write(
        tmp_path,
        {"name": "", "steps": [{"adapter": "Nope", "prompt": "x"}]},
    )
    with pytest.raises(WorkflowError) as exc_info:
        load_workflow(path, known_adapters=KNOWN)
    message = str(exc_info.value)
    assert "'name' must be a non-empty string" in message
    assert "unknown adapter 'Nope'" in message


# --- validation (collects ALL problems) -----------------------------------


def test_validate_top_level_not_object():
    assert validate_workflow([1, 2, 3]) == ["top level must be a JSON object"]


def test_validate_missing_name():
    problems = validate_workflow(
        {"steps": [{"adapter": "EchoAdapter", "prompt": "x"}]}
    )
    assert "'name' must be a non-empty string" in problems


def test_validate_name_wrong_type():
    problems = validate_workflow(
        {"name": 123, "steps": [{"adapter": "EchoAdapter", "prompt": "x"}]}
    )
    assert "'name' must be a non-empty string" in problems


def test_validate_steps_not_list():
    assert "'steps' must be a list" in validate_workflow({"name": "d", "steps": "x"})


def test_validate_steps_empty():
    assert "'steps' must not be empty" in validate_workflow({"name": "d", "steps": []})


def test_validate_step_not_object():
    assert "step 1 must be a JSON object" in validate_workflow(
        {"name": "d", "steps": [42]}
    )


def test_validate_step_missing_adapter():
    problems = validate_workflow({"name": "d", "steps": [{"prompt": "x"}]})
    assert "step 1: 'adapter' must be a non-empty string" in problems


def test_validate_step_bad_prompt():
    problems = validate_workflow(
        {"name": "d", "steps": [{"adapter": "EchoAdapter", "prompt": 5}]}
    )
    assert "step 1: 'prompt' must be a string" in problems


def test_validate_unknown_adapter_when_known_given():
    problems = validate_workflow(
        {"name": "d", "steps": [{"adapter": "Nope", "prompt": "x"}]},
        known_adapters=KNOWN,
    )
    assert "step 1: unknown adapter 'Nope'" in problems


def test_validate_unknown_adapter_skipped_without_known():
    problems = validate_workflow(
        {"name": "d", "steps": [{"adapter": "Nope", "prompt": "x"}]}
    )
    assert problems == []


@pytest.mark.parametrize("bad", [0, -5, "5", True, [1], 0.0])
def test_validate_bad_timeout(bad):
    problems = validate_workflow(
        {"name": "d", "steps": [{"adapter": "EchoAdapter", "prompt": "x", "timeout": bad}]}
    )
    assert "step 1: 'timeout' must be a positive number" in problems


def test_validate_good_timeout_accepted():
    problems = validate_workflow(
        {"name": "d", "steps": [{"adapter": "EchoAdapter", "prompt": "x", "timeout": 2.5}]},
        known_adapters=KNOWN,
    )
    assert problems == []


def test_validate_reports_every_problem_at_once():
    data = {
        "name": "",
        "steps": [
            {"adapter": "Nope", "prompt": "x"},
            {"adapter": "EchoAdapter", "prompt": 5},
            {"adapter": "EchoAdapter", "prompt": "y", "timeout": -1},
        ],
    }
    problems = validate_workflow(data, known_adapters=KNOWN)
    assert "'name' must be a non-empty string" in problems
    assert "step 1: unknown adapter 'Nope'" in problems
    assert "step 2: 'prompt' must be a string" in problems
    assert "step 3: 'timeout' must be a positive number" in problems
    assert len(problems) == 4
