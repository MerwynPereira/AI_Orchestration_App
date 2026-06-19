# CLAUDE.md

Guidance for working in this repository. It documents the conventions the
`conductor/` engine actually follows today — match them when extending it.

## Purpose

Conductor is a small engine for chaining AI/CLI tools. A **workflow** is an
ordered list of **steps**; each step names an **adapter** (a uniform wrapper
around a tool) and a **prompt**. The **runner** executes steps in order, piping
each step's output into the next step's prompt: `{input}` is the
immediately-previous output, and `{steps.<id>}` is the output of any earlier
step that declared that `id` (see "Prompt placeholders" below). The engine is
headless and command-line driven — there is no UI here.

The **core engine** (adapters base, registry, workflow, runner, CLI) is
stdlib-only. The engine is no longer *strictly* stdlib-only overall: the
GUI-chat adapters need `pywinauto` + `pyperclip` (see `requirements.txt`), but
they are imported lazily so the engine and the test suite still run without them
installed.

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

Three flavours of adapter exist, and "prompt" means different things:

- **Chat-style** (e.g. `ClaudeCodeAdapter`): `prompt` is natural language; the
  return value is the model's reply.
- **Editor-style** (e.g. `VSCodeAdapter`, `AntigravityEditorAdapter`): `prompt`
  is a string of **CLI arguments** for the editor (e.g. `--diff a.txt b.txt`),
  NOT a chat prompt and NOT an AI feature. These never invoke a tool's AI agent
  and must not pass `--wait` (it blocks until the window closes).
- **GUI-chat** (e.g. `ClaudeDesktopAdapter`): `prompt` is natural language
  driven into a desktop chat *window* via focus + clipboard; the reply is read
  back by polling until the text settles. See `GuiChatAdapter` below.

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

GUI-chat adapters subclass `GuiChatAdapter` and only provide three physical
actions, each of which must raise `AdapterError` (never a raw pywinauto/OS
error):

- `_focus_window()` — bring the target window to the foreground.
- `_submit_prompt(prompt)` — paste the prompt into the message box and submit.
- `_read_response() -> str` — return the current reply text (`""` until it
  begins); called repeatedly while polling.

`GuiChatAdapter.send()` guards an empty prompt, runs focus → submit (wrapping any
non-`AdapterError` in `AdapterError`), then calls the pure module-level
`_poll_until_stable()` helper. That helper is the **response-complete
heuristic**: a streaming reply keeps growing, so once `_read_response()` returns
the same non-empty text for `stable_for` seconds it is judged finished;
`overall_timeout` is the hard wall. `_now`/`_sleep` are injectable so tests drive
a deterministic fake clock. Optional GUI deps are imported lazily via
`_import_module()`, which converts a missing package into `AdapterError`.

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
    { "id": "draft", "adapter": "EchoAdapter", "prompt": "Hello" },
    { "adapter": "ClaudeCodeAdapter", "prompt": "Improve: {steps.draft}", "timeout": 60 }
  ]
}
```

- `name` — non-empty string.
- `steps` — non-empty list. Each step:
  - `adapter` — a registered adapter name.
  - `prompt` — string; see "Prompt placeholders" below.
  - `timeout` — optional positive number (seconds) overriding the adapter
    default; only meaningful for CLI adapters.
  - `id` — optional unique identifier for this step's output, letting later
    steps reference it as `{steps.<id>}`. Allowed characters: letters, digits,
    `_`, `-`.
  - `retries` — optional non-negative integer (default `0`). On `AdapterError`
    the runner retries the step up to this many extra times with a small fixed
    backoff (`RETRY_BACKOFF_SECONDS`); if it still fails, the usual
    `step N (Adapter) failed` behaviour applies. The backoff `sleep` is
    injected into `run_workflow`, so tests never actually wait.
- Unknown keys (e.g. `_comment`) are ignored, so JSON files can carry notes.

### Prompt placeholders

The runner substitutes two placeholder kinds into a prompt, in a single pass
(so substituted text is never re-scanned):

- `{input}` — the immediately-previous step's output (empty for the first
  step). Behaviour is unchanged from the original linear engine.
- `{steps.<id>}` — the output of an earlier step that declared `"id": "<id>"`.
  This is what enables fan-in: a step can combine several earlier outputs, not
  just the previous one.

Any other braces (e.g. literal JSON `{"k": 1}`) are left untouched. Validation
(in the all-problems-at-once style) reports duplicate ids, references to unknown
ids, and forward/self references (an id used before the step that produces it).
At runtime an unresolved id falls back to an empty string, but load-time
validation is the real guard.

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
The core engine needs no third-party packages; the GUI-chat adapters need the
runtime deps in `requirements.txt` (`pywinauto`, `pyperclip`) but only to drive a
real window — the test suite mocks the automation layer and runs without them.
Dev tooling (pytest) lives in `requirements-dev.txt`.
