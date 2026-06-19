"""Tests for the command-line entry point (``conductor.__main__``)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from conductor import __main__ as cli

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
