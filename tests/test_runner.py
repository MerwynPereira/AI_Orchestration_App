"""Tests for the workflow runner."""

from __future__ import annotations

import logging
import subprocess

import pytest

from conductor.adapters import Adapter, AdapterError, ClaudeCodeAdapter, EchoAdapter
from conductor.runner import (
    RETRY_BACKOFF_SECONDS,
    _resolve_prompt,
    _send_with_retries,
    run_workflow,
)
from conductor.workflow import Step, Workflow, WorkflowError


class _BoomAdapter(Adapter):
    """Always fails — used to test stop-on-error reporting."""

    def send(self, prompt: str) -> str:
        raise AdapterError("kaboom")


class _FlakyAdapter(Adapter):
    """Fails its first ``fail_times`` calls, then succeeds.

    The runner reuses one adapter instance across a step's retry attempts, so an
    instance counter tracks attempts within a single step.
    """

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def send(self, prompt: str) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise AdapterError(f"transient {self.calls}")
        return f"ok after {self.calls}"


class _FlakyOnceAdapter(_FlakyAdapter):
    """No-arg flaky adapter (fails once) so the registry can instantiate it."""

    def __init__(self) -> None:
        super().__init__(fail_times=1)


def _echo_registry() -> dict[str, type[Adapter]]:
    return {"EchoAdapter": EchoAdapter, "BoomAdapter": _BoomAdapter}


# --- prompt resolution -----------------------------------------------------


def test_resolve_prompt_substitutes_input():
    step = Step(adapter="EchoAdapter", prompt="got: {input}")
    assert _resolve_prompt(step, "PREV") == "got: PREV"


def test_resolve_prompt_without_placeholder():
    step = Step(adapter="EchoAdapter", prompt="plain")
    assert _resolve_prompt(step, "PREV") == "plain"


def test_resolve_prompt_substitutes_named_outputs():
    step = Step(adapter="EchoAdapter", prompt="{steps.a} + {steps.b}")
    assert _resolve_prompt(step, "PREV", {"a": "X", "b": "Y"}) == "X + Y"


def test_resolve_prompt_mixes_input_and_named_outputs():
    step = Step(adapter="EchoAdapter", prompt="{input} then {steps.a}")
    assert _resolve_prompt(step, "PREV", {"a": "X"}) == "PREV then X"


def test_resolve_prompt_unknown_named_output_is_empty():
    step = Step(adapter="EchoAdapter", prompt="[{steps.missing}]")
    assert _resolve_prompt(step, "PREV", {}) == "[]"


def test_resolve_prompt_does_not_rescan_substituted_text():
    # An injected value that contains a placeholder must NOT be re-expanded.
    step = Step(adapter="EchoAdapter", prompt="{input}")
    assert _resolve_prompt(step, "{steps.a}", {"a": "SECRET"}) == "{steps.a}"


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


def test_named_outputs_let_a_step_combine_two_earlier_outputs():
    workflow = Workflow(
        name="fan-in",
        steps=[
            Step(adapter="EchoAdapter", prompt="alpha", id="a"),
            Step(adapter="EchoAdapter", prompt="beta", id="b"),
            Step(adapter="EchoAdapter", prompt="{steps.a}|{steps.b}|{input}"),
        ],
    )
    results = run_workflow(workflow, registry=_echo_registry())
    # The last step sees both named outputs and {input} (= the prior step "beta").
    assert results[-1].output == "alpha|beta|beta"


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


# --- retries ---------------------------------------------------------------


def test_retry_succeeds_after_transient_failures():
    slept: list[float] = []
    adapter = _FlakyAdapter(fail_times=2)
    step = Step(adapter="Flaky", prompt="p", retries=2)

    output = _send_with_retries(adapter, "p", step, 1, slept.append)

    assert output == "ok after 3"
    assert adapter.calls == 3
    # One backoff between each of the 3 attempts (no sleep after the success).
    assert slept == [RETRY_BACKOFF_SECONDS, RETRY_BACKOFF_SECONDS]


def test_retry_exhausted_raises_workflow_error_naming_step():
    slept: list[float] = []
    adapter = _FlakyAdapter(fail_times=99)
    step = Step(adapter="Flaky", prompt="p", retries=2)

    with pytest.raises(WorkflowError) as exc_info:
        _send_with_retries(adapter, "p", step, 4, slept.append)

    message = str(exc_info.value)
    assert "step 4 (Flaky) failed" in message
    assert "transient 3" in message  # the last attempt's error
    assert adapter.calls == 3  # retries=2 => 3 attempts total
    assert slept == [RETRY_BACKOFF_SECONDS, RETRY_BACKOFF_SECONDS]


def test_no_retries_means_single_attempt_and_no_sleep():
    slept: list[float] = []
    adapter = _FlakyAdapter(fail_times=1)
    step = Step(adapter="Flaky", prompt="p")  # retries defaults to 0

    with pytest.raises(WorkflowError):
        _send_with_retries(adapter, "p", step, 1, slept.append)

    assert adapter.calls == 1
    assert slept == []


def test_run_workflow_retries_a_flaky_step():
    slept: list[float] = []
    registry = {"Flaky": _FlakyOnceAdapter}
    workflow = Workflow(name="w", steps=[Step(adapter="Flaky", prompt="p", retries=1)])

    results = run_workflow(workflow, registry=registry, sleep=slept.append)

    assert results[0].output == "ok after 2"
    assert slept == [RETRY_BACKOFF_SECONDS]


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
