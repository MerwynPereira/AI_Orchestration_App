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


# --- step id + {steps.<id>} references -------------------------------------


def test_load_parses_step_id(tmp_path):
    path = _write(
        tmp_path,
        {
            "name": "d",
            "steps": [
                {"id": "first", "adapter": "EchoAdapter", "prompt": "hi"},
                {"adapter": "EchoAdapter", "prompt": "{steps.first}"},
            ],
        },
    )
    steps = load_workflow(path, known_adapters=KNOWN).steps
    assert steps[0].id == "first"
    assert steps[1].id is None


@pytest.mark.parametrize("bad", [123, True, [1], {"x": 1}])
def test_validate_id_wrong_type(bad):
    problems = validate_workflow(
        {"name": "d", "steps": [{"id": bad, "adapter": "EchoAdapter", "prompt": "x"}]}
    )
    assert "step 1: 'id' must be a non-empty string" in problems


def test_validate_id_empty_string():
    problems = validate_workflow(
        {"name": "d", "steps": [{"id": "", "adapter": "EchoAdapter", "prompt": "x"}]}
    )
    assert "step 1: 'id' must be a non-empty string" in problems


@pytest.mark.parametrize("bad", ["has space", "dot.name", "bang!", "a/b"])
def test_validate_id_bad_characters(bad):
    problems = validate_workflow(
        {"name": "d", "steps": [{"id": bad, "adapter": "EchoAdapter", "prompt": "x"}]}
    )
    assert "step 1: 'id' may contain only letters, digits, '_' and '-'" in problems


@pytest.mark.parametrize("good", ["first", "step_1", "step-2", "ABC123"])
def test_validate_id_good_accepted(good):
    problems = validate_workflow(
        {"name": "d", "steps": [{"id": good, "adapter": "EchoAdapter", "prompt": "x"}]},
        known_adapters=KNOWN,
    )
    assert problems == []


def test_validate_duplicate_ids():
    problems = validate_workflow(
        {
            "name": "d",
            "steps": [
                {"id": "dup", "adapter": "EchoAdapter", "prompt": "a"},
                {"id": "dup", "adapter": "EchoAdapter", "prompt": "b"},
            ],
        },
        known_adapters=KNOWN,
    )
    assert "step 2: duplicate step id 'dup'" in problems


def test_validate_reference_to_known_earlier_id_ok():
    problems = validate_workflow(
        {
            "name": "d",
            "steps": [
                {"id": "a", "adapter": "EchoAdapter", "prompt": "x"},
                {"adapter": "EchoAdapter", "prompt": "uses {steps.a}"},
            ],
        },
        known_adapters=KNOWN,
    )
    assert problems == []


def test_validate_reference_to_unknown_id():
    problems = validate_workflow(
        {
            "name": "d",
            "steps": [{"adapter": "EchoAdapter", "prompt": "uses {steps.ghost}"}],
        },
        known_adapters=KNOWN,
    )
    assert "step 1: prompt references unknown step id 'ghost'" in problems


def test_validate_forward_reference():
    problems = validate_workflow(
        {
            "name": "d",
            "steps": [
                {"adapter": "EchoAdapter", "prompt": "uses {steps.later}"},
                {"id": "later", "adapter": "EchoAdapter", "prompt": "x"},
            ],
        },
        known_adapters=KNOWN,
    )
    assert "step 1: prompt references step id 'later' before it runs" in problems


def test_validate_self_reference_is_forward():
    problems = validate_workflow(
        {
            "name": "d",
            "steps": [{"id": "me", "adapter": "EchoAdapter", "prompt": "{steps.me}"}],
        },
        known_adapters=KNOWN,
    )
    assert "step 1: prompt references step id 'me' before it runs" in problems


def test_validate_non_reference_braces_are_ignored():
    # Literal braces that are not {input} or {steps.<id>} must pass through.
    problems = validate_workflow(
        {
            "name": "d",
            "steps": [{"adapter": "EchoAdapter", "prompt": 'json: {"k": 1} and {x}'}],
        },
        known_adapters=KNOWN,
    )
    assert problems == []
