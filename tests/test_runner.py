"""Tests for the workflow runner."""

from __future__ import annotations

import logging
import subprocess

import pytest

from conductor.adapters import Adapter, AdapterError, ClaudeCodeAdapter, EchoAdapter
from conductor.runner import _resolve_prompt, run_workflow
from conductor.workflow import Step, Workflow, WorkflowError


class _BoomAdapter(Adapter):
    """Always fails — used to test stop-on-error reporting."""

    def send(self, prompt: str) -> str:
        raise AdapterError("kaboom")


def _echo_registry() -> dict[str, type[Adapter]]:
    return {"EchoAdapter": EchoAdapter, "BoomAdapter": _BoomAdapter}


# --- prompt resolution -----------------------------------------------------


def test_resolve_prompt_substitutes_input():
    step = Step(adapter="EchoAdapter", prompt="got: {input}")
    assert _resolve_prompt(step, "PREV") == "got: PREV"


def test_resolve_prompt_without_placeholder():
    step = Step(adapter="EchoAdapter", prompt="plain")
    assert _resolve_prompt(step, "PREV") == "plain"


# --- chaining --------------------------------------------------------------


def test_two_step_chain_pipes_output_into_next_input():
    workflow = Workflow(
        name="chain",
        steps=[
            Step(adapter="EchoAdapter", prompt="first"),
            Step(adapter="EchoAdapter", prompt="got: {input}"),
        ],
    )
    results = run_workflow(workflow, registry=_echo_registry())
    assert [r.output for r in results] == ["first", "got: first"]
    assert [r.index for r in results] == [1, 2]


# --- stop-on-error ---------------------------------------------------------


def test_failure_stops_and_reports_which_step():
    workflow = Workflow(
        name="fail",
        steps=[
            Step(adapter="EchoAdapter", prompt="ok"),
            Step(adapter="BoomAdapter", prompt="this fails"),
            Step(adapter="EchoAdapter", prompt="never runs"),
        ],
    )
    with pytest.raises(WorkflowError) as exc_info:
        run_workflow(workflow, registry=_echo_registry())
    message = str(exc_info.value)
    assert "step 2 (BoomAdapter) failed" in message
    assert "kaboom" in message


def test_unknown_adapter_reports_step_index():
    workflow = Workflow(name="x", steps=[Step(adapter="Ghost", prompt="x")])
    with pytest.raises(WorkflowError) as exc_info:
        run_workflow(workflow, registry={"EchoAdapter": EchoAdapter})
    assert "step 1: unknown adapter 'Ghost'" in str(exc_info.value)


# --- per-step timeout ------------------------------------------------------


def test_per_step_timeout_overrides_adapter_default(monkeypatch):
    calls: list[dict] = []

    def fake_run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("conductor.adapters.subprocess.run", fake_run)
    workflow = Workflow(
        name="t",
        steps=[Step(adapter="ClaudeCodeAdapter", prompt="x", timeout=7)],
    )
    run_workflow(workflow, registry={"ClaudeCodeAdapter": ClaudeCodeAdapter})
    assert calls[0]["timeout"] == 7


def test_default_timeout_used_when_no_override(monkeypatch):
    calls: list[dict] = []

    def fake_run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("conductor.adapters.subprocess.run", fake_run)
    workflow = Workflow(
        name="t",
        steps=[Step(adapter="ClaudeCodeAdapter", prompt="x")],
    )
    run_workflow(workflow, registry={"ClaudeCodeAdapter": ClaudeCodeAdapter})
    assert calls[0]["timeout"] == 120.0


# --- logging ---------------------------------------------------------------


def test_runner_logs_progress_at_info(caplog):
    workflow = Workflow(name="demo", steps=[Step(adapter="EchoAdapter", prompt="hi")])
    with caplog.at_level(logging.INFO, logger="conductor.runner"):
        run_workflow(workflow, registry={"EchoAdapter": EchoAdapter})
    text = caplog.text
    assert "Running workflow: demo" in text
    assert "Step 1: EchoAdapter" in text
    assert "output: hi" in text
    assert "Workflow complete" in text


def test_runner_logs_prompt_only_at_debug(caplog):
    workflow = Workflow(
        name="demo", steps=[Step(adapter="EchoAdapter", prompt="secret-prompt")]
    )
    with caplog.at_level(logging.DEBUG, logger="conductor.runner"):
        run_workflow(workflow, registry={"EchoAdapter": EchoAdapter})
    assert "prompt: secret-prompt" in caplog.text


def test_runner_hides_prompt_at_info(caplog):
    workflow = Workflow(
        name="demo", steps=[Step(adapter="EchoAdapter", prompt="secret-prompt")]
    )
    with caplog.at_level(logging.INFO, logger="conductor.runner"):
        run_workflow(workflow, registry={"EchoAdapter": EchoAdapter})
    # The DEBUG-only "prompt:" line is not emitted at INFO. (EchoAdapter echoes
    # its input, so the prompt text still appears in the "output:" line.)
    assert "prompt: secret-prompt" not in caplog.text
