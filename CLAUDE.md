# CLAUDE.md

Guidance for working in this repository. It documents the conventions the
`conductor/` engine actually follows today — match them when extending it.

## Purpose

Conductor is a small, stdlib-only engine for chaining AI/CLI tools. A
**workflow** is an ordered list of **steps**; each step names an **adapter** (a
uniform wrapper around a tool) and a **prompt**. The **runner** executes steps
in order, piping each step's output into the next step's prompt via an
`{input}` placeholder. The engine is headless and command-line driven — there
is no UI and no GUI-automation here, by design.

## Architecture

The package is four cooperating modules plus a CLI:

- **`conductor/adapters.py`** — the `Adapter` ABC and concrete adapters. All
  CLI-backed adapters share `CliAdapter`, which centralises subprocess
  hardening.
- **`conductor/registry.py`** — `ADAPTERS`, the single name→class mapping, plus
  `create_adapter()` and `adapter_names()`.
- **`conductor/workflow.py`** — the `Step`/`Workflow` dataclasses,
  `validate_workflow()` (collects *all* problems), and `load_workflow()`.
- **`conductor/runner.py`** — `run_workflow()` / `StepResult`; executes and logs
  steps, chaining outputs to inputs.
- **`conductor/__main__.py`** — the `argparse` CLI (`python -m conductor`).

Dependency direction (no cycles): `adapters` ← `registry` ← `runner` ←
`__main__`; `workflow` is standalone and takes known adapter names as data.

## The adapter contract

Every adapter subclasses `Adapter` and implements:

```python
def send(self, prompt: str) -> str: ...
```

- Return the response as a `str`.
- On any failure, raise `AdapterError` (never let a raw `OSError`,
  `subprocess.SubprocessError`, etc. escape). Chain the cause with
  `raise AdapterError(...) from exc`.
- `prompt` arrives fully resolved (the runner has already substituted
  `{input}`).

Two flavours of adapter exist, and "prompt" means different things:

- **Chat-style** (e.g. `ClaudeCodeAdapter`): `prompt` is natural language; the
  return value is the model's reply.
- **Editor-style** (e.g. `VSCodeAdapter`, `AntigravityEditorAdapter`): `prompt`
  is a string of **CLI arguments** for the editor (e.g. `--diff a.txt b.txt`),
  NOT a chat prompt and NOT an AI feature. These never invoke a tool's AI agent
  and must not pass `--wait` (it blocks until the window closes).

CLI adapters subclass `CliAdapter` and only provide:

- `tool_name` — short name used in error messages.
- `default_executable` — absolute path to the binary/launcher.
- `_build_args(prompt) -> list[str]` — args after the executable.
- optionally `_format_output(output, args) -> str`.

`CliAdapter.send()` then handles `subprocess.run` with: args as a list (never
`shell=True`), `capture_output=True`, `text=True`, `encoding="utf-8"`,
`errors="replace"`, `stdin=subprocess.DEVNULL`, and a `timeout`. It raises
`AdapterError` for a missing executable, a non-zero exit, or a timeout, and
returns stdout passed through `_clean()` (ANSI-stripped, whitespace-trimmed).
The `timeout` attribute is public and mutable so the runner can apply a per-step
override.

## How to add an adapter

1. Implement the class in `conductor/adapters.py` (subclass `Adapter`, or
   `CliAdapter` for a tool you shell out to).
2. Register it with one line in `conductor/registry.py`'s `ADAPTERS` dict,
   keyed by the class name.
3. Add tests in `tests/` — **mock `subprocess.run`**; never call a real binary.

The workflow JSON refers to the adapter by that registered name.

## Workflow JSON

```json
{
  "name": "my-workflow",
  "steps": [
    { "adapter": "EchoAdapter", "prompt": "Hello" },
    { "adapter": "ClaudeCodeAdapter", "prompt": "Improve: {input}", "timeout": 60 }
  ]
}
```

- `name` — non-empty string.
- `steps` — non-empty list. Each step:
  - `adapter` — a registered adapter name.
  - `prompt` — string; `{input}` is replaced with the previous step's output.
  - `timeout` — optional positive number (seconds) overriding the adapter
    default; only meaningful for CLI adapters.
- Unknown keys (e.g. `_comment`) are ignored, so JSON files can carry notes.

## Error-handling style

- `AdapterError` — an adapter failed to produce a response.
- `WorkflowError` — a workflow is missing/malformed/invalid, or a step failed.
  The runner wraps an adapter's `AdapterError` in a `WorkflowError` that names
  the failing step (`step N (AdapterName) failed: ...`) and stops; later steps
  do not run.
- Validation reports **all** problems at once. `validate_workflow(data,
  known_adapters)` returns a list of human-readable problems;
  `load_workflow()` raises a single `WorkflowError` listing every problem.
  Only truly unrecoverable issues (file not found, invalid JSON) fail fast.

## Logging

The runner uses the stdlib `logging` module via a module-level
`logger = logging.getLogger(__name__)` — no bare `print()`. Step headers and
outputs log at `INFO`; the resolved prompt logs at `DEBUG`. The CLI routes
logs to stdout as plain lines and exposes `--verbose` (`-v`) for `DEBUG`. Tests
assert on output via pytest's `caplog`. CLI-level messages (load errors, the
dry-run plan) use `print()` to stderr/stdout intentionally.

## Coding conventions

- `from __future__ import annotations` at the top of every module.
- Full type hints; modern syntax (`list[Step]`, `X | None`).
- Small, single-purpose functions; module/public functions and classes carry
  docstrings (Google-style Args/Returns/Raises).
- Frozen dataclasses for plain data (`Step`, `Workflow`, `StepResult`).
- Private helpers are `_underscore`-prefixed.

## Running and testing

```bash
# Run a workflow
python -m conductor example_workflow.json
python -m conductor --dry-run example_timeout_workflow.json   # validate + plan only
python -m conductor --verbose example_workflow.json           # show resolved prompts

# Tests (pytest is the only dev dependency)
pip install -r requirements-dev.txt
python -m pytest
```

On this machine use the project venv: `.\venv\Scripts\python.exe -m pytest`.
The engine itself needs no third-party packages (`requirements.txt` is empty);
dev tooling lives in `requirements-dev.txt`.
