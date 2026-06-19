"""Tests for the command-line entry point (``conductor.__main__``)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from conductor import __main__ as cli
from conductor.runner import StepResult
from conductor.workflow import Step, Workflow

ROOT = Path(__file__).resolve().parent.parent


def _write(tmp_path, data, name="wf.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- happy path ------------------------------------------------------------


def test_main_runs_workflow_and_returns_zero(tmp_path, capsys):
    path = _write(
        tmp_path,
        {
            "name": "demo",
            "steps": [
                {"adapter": "EchoAdapter", "prompt": "hi"},
                {"adapter": "EchoAdapter", "prompt": "got: {input}"},
            ],
        },
    )
    assert cli.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "Step 1: EchoAdapter" in out
    assert "got: hi" in out


# --- errors ----------------------------------------------------------------


def test_main_missing_file_returns_one(tmp_path, capsys):
    assert cli.main([str(tmp_path / "nope.json")]) == 1
    err = capsys.readouterr().err
    assert "Failed to load workflow" in err
    assert "not found" in err


def test_main_invalid_workflow_reports_all_problems(tmp_path, capsys):
    path = _write(
        tmp_path,
        {
            "name": "d",
            "steps": [
                {"adapter": "Nope", "prompt": "x"},
                {"adapter": "EchoAdapter", "prompt": "y", "timeout": -1},
            ],
        },
    )
    assert cli.main([str(path)]) == 1
    err = capsys.readouterr().err
    assert "unknown adapter 'Nope'" in err
    assert "'timeout' must be a positive number" in err


def test_main_no_args_exits_two():
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])
    assert exc_info.value.code == 2


# --- dry run ---------------------------------------------------------------


def test_main_dry_run_executes_nothing(tmp_path, capsys, monkeypatch):
    path = _write(
        tmp_path,
        {
            "name": "demo",
            "steps": [{"adapter": "EchoAdapter", "prompt": "hi", "timeout": 5}],
        },
    )
    spy = mock.MagicMock()
    monkeypatch.setattr(cli, "run_workflow", spy)

    assert cli.main(["--dry-run", str(path)]) == 0
    spy.assert_not_called()
    out = capsys.readouterr().out
    assert "Plan for workflow: demo" in out
    assert "1. EchoAdapter (timeout=5.0s)" in out
    assert "prompt: hi" in out


def test_main_dry_run_invalid_returns_one(tmp_path, capsys):
    path = _write(tmp_path, {"name": "d", "steps": [{"adapter": "Nope", "prompt": "x"}]})
    assert cli.main(["--dry-run", str(path)]) == 1
    assert "unknown adapter 'Nope'" in capsys.readouterr().err


# --- verbosity -------------------------------------------------------------


def test_main_verbose_shows_prompt(tmp_path, capsys):
    path = _write(
        tmp_path,
        {"name": "d", "steps": [{"adapter": "EchoAdapter", "prompt": "secret-xyz"}]},
    )
    assert cli.main(["--verbose", str(path)]) == 0
    assert "prompt: secret-xyz" in capsys.readouterr().out


def test_main_non_verbose_hides_prompt(tmp_path, capsys):
    path = _write(
        tmp_path,
        {"name": "d", "steps": [{"adapter": "EchoAdapter", "prompt": "secret-xyz"}]},
    )
    assert cli.main([str(path)]) == 0
    # Without --verbose the resolved-prompt (DEBUG) line is suppressed. The
    # echoed text still shows up in the "output:" line, so check the label.
    assert "prompt: secret-xyz" not in capsys.readouterr().out


# --- run log (--output) ----------------------------------------------------


def test_main_output_writes_run_log(tmp_path, capsys):
    wf = _write(
        tmp_path,
        {
            "name": "logged",
            "steps": [
                {"id": "a", "adapter": "EchoAdapter", "prompt": "first"},
                {"adapter": "EchoAdapter", "prompt": "got: {steps.a}"},
            ],
        },
    )
    out_path = tmp_path / "results.json"
    assert cli.main([str(wf), "--output", str(out_path)]) == 0
    assert f"Wrote run log to {out_path}" in capsys.readouterr().out

    log = json.loads(out_path.read_text(encoding="utf-8"))
    assert log["workflow"] == "logged"
    assert log["result"] == "ok"
    assert [s["index"] for s in log["steps"]] == [1, 2]
    assert log["steps"][0]["id"] == "a"
    assert log["steps"][1]["id"] is None
    assert log["steps"][1]["output"] == "got: first"
    assert all(s["status"] == "ok" for s in log["steps"])
    assert all(isinstance(s["duration_seconds"], (int, float)) for s in log["steps"])
    assert all(s["duration_seconds"] >= 0 for s in log["steps"])


def test_main_output_failure_returns_one(tmp_path, capsys):
    wf = _write(
        tmp_path,
        {"name": "d", "steps": [{"adapter": "EchoAdapter", "prompt": "hi"}]},
    )
    # Parent directory does not exist -> write raises OSError.
    bad = tmp_path / "missing_dir" / "out.json"
    assert cli.main([str(wf), "--output", str(bad)]) == 1
    assert "Failed to write run log" in capsys.readouterr().err


def test_build_run_log_marks_overall_error_when_a_step_failed():
    workflow = Workflow(
        name="mixed",
        steps=[
            Step(adapter="EchoAdapter", prompt="ok", id="a"),
            Step(adapter="BoomAdapter", prompt="x", continue_on_error=True),
        ],
    )
    results = [
        StepResult(index=1, adapter="EchoAdapter", output="ok", duration=0.01),
        StepResult(
            index=2,
            adapter="BoomAdapter",
            output="",
            status="error",
            error="step 2 (BoomAdapter) failed: kaboom",
            duration=0.02,
        ),
    ]
    log = cli._build_run_log(workflow, results)
    assert log["result"] == "error"
    assert log["steps"][0]["id"] == "a"
    assert log["steps"][1]["status"] == "error"
    assert "kaboom" in log["steps"][1]["error"]


# --- bundled example workflows --------------------------------------------


def test_example_echo_workflow_runs():
    assert cli.main([str(ROOT / "example_workflow.json")]) == 0


def test_example_named_outputs_workflow_runs(capsys):
    assert cli.main([str(ROOT / "example_named_outputs_workflow.json")]) == 0
    out = capsys.readouterr().out
    # The final step interpolates both named outputs by id.
    assert "Explain distributed systems to first-year students" in out


def test_example_invalid_workflow_fails(capsys):
    assert cli.main([str(ROOT / "example_invalid_workflow.json")]) == 1
    assert "unknown adapter 'NoSuchAdapter'" in capsys.readouterr().err


def test_example_timeout_workflow_parses(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "run_workflow", mock.MagicMock())
    assert cli.main(["--dry-run", str(ROOT / "example_timeout_workflow.json")]) == 0
    assert "timeout=60.0s" in capsys.readouterr().out


def test_example_resilient_workflow_parses(capsys, monkeypatch):
    # Uses ClaudeCodeAdapter; just validate + plan it without running.
    monkeypatch.setattr(cli, "run_workflow", mock.MagicMock())
    assert cli.main(["--dry-run", str(ROOT / "example_resilient_workflow.json")]) == 0
    assert "[id=facts]" in capsys.readouterr().out
