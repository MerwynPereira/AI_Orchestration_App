"""Flet desktop shell for Conductor — a thin layer over the headless engine.

This module is deliberately thin: all display *logic* lives in
:mod:`conductor.ui.presenter` (pure and unit-tested). Here we only wire engine
calls to Flet controls. As the GUI-chat diagnostics import ``pywinauto``
directly, this UI module imports ``flet`` directly; the core engine stays
stdlib-only and never imports this package.

Engine surface used (public API only — no validation or execution is
reimplemented):

* :func:`conductor.workflow.load_workflow` / :class:`~conductor.workflow.Workflow`
  / :class:`~conductor.workflow.WorkflowError`
* :func:`conductor.runner.run_workflow`
* :data:`conductor.registry.ADAPTERS`

Run it with ``python -m conductor.ui`` (after ``pip install -r
requirements-ui.txt``). Verified against Flet 0.85.x.
"""

from __future__ import annotations

import asyncio

import flet as ft

from conductor.registry import ADAPTERS
from conductor.runner import StepResult, run_workflow
from conductor.workflow import Workflow, WorkflowError, load_workflow

from . import presenter

_ERROR_COLOR = "red"
_OK_COLOR = "green"


def main(page: ft.Page) -> None:
    """Build and wire the single-screen Conductor UI.

    Passed to :func:`flet.run`. Sets up the controls and attaches async event
    handlers; the handlers call :mod:`conductor.ui.presenter` for all text.

    Args:
        page: The Flet page provided by the runtime.
    """
    page.title = "Conductor"
    page.scroll = ft.ScrollMode.AUTO

    # The currently loaded workflow (None until a valid file is opened). Reassigned
    # by ``on_open`` via ``nonlocal``; read by ``_set_busy`` and ``on_run``.
    loaded: Workflow | None = None

    title = ft.Text("Conductor", size=24)
    subtitle = ft.Text("Open a workflow, review its plan, then Run.", size=12)

    open_button = ft.Button("Open workflow…")
    run_button = ft.Button("Run", disabled=True)
    progress = ft.ProgressRing(visible=False, width=18, height=18)
    running_text = ft.Text("Running…", visible=False)

    plan_view = ft.Column(spacing=6)
    results_view = ft.Column(spacing=6)

    # FilePicker is a page service in current Flet; register it once and reuse it.
    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    def _set_busy(busy: bool) -> None:
        """Toggle the run/loading state of the controls (does not call update)."""
        open_button.disabled = busy
        run_button.disabled = busy or loaded is None
        progress.visible = busy
        running_text.visible = busy

    async def on_open(_event: ft.Event[ft.Button]) -> None:
        """Pick a .json file, load it, and render either its plan or its errors."""
        nonlocal loaded
        files = await file_picker.pick_files(
            dialog_title="Open a Conductor workflow",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["json"],
            allow_multiple=False,
        )
        if not files:
            return  # user cancelled the dialog

        results_view.controls.clear()
        path = files[0].path
        if path is None:  # web mode has no local path; this UI is desktop-only
            loaded = None
            run_button.disabled = True
            plan_view.controls = [
                ft.Text("Could not read the selected file's path.", color=_ERROR_COLOR)
            ]
            page.update()
            return

        try:
            workflow = load_workflow(path, known_adapters=ADAPTERS)
        except WorkflowError as exc:
            # Surface the engine's own all-problems-at-once messages; keep Run off.
            loaded = None
            run_button.disabled = True
            plan_view.controls = [
                ft.Text("This workflow can't be loaded:", color=_ERROR_COLOR)
            ] + [
                ft.Text(f"• {problem}", color=_ERROR_COLOR)
                for problem in presenter.format_load_error(exc)
            ]
            page.update()
            return

        loaded = workflow
        run_button.disabled = False
        plan_view.controls = _build_plan_controls(workflow)
        page.update()

    async def on_run(_event: ft.Event[ft.Button]) -> None:
        """Run the loaded workflow off the UI thread, then render its results."""
        if loaded is None:
            return

        results_view.controls.clear()
        _set_busy(True)
        page.update()

        try:
            # run_workflow is BLOCKING (subprocesses, retry backoff sleeps, GUI
            # polling) — running it on the UI event loop would freeze the window.
            # The current Flet API is async-first, so we offload it to a worker
            # thread via the default executor; awaiting marshals the result (or a
            # raised WorkflowError) back onto the UI event loop, where mutating
            # controls is safe. This realises the "run on a thread + marshal the
            # result/error back before updating" requirement using async Flet.
            #
            # TODO(live-progress): the runner already emits per-step INFO logs; a
            # follow-up can attach a logging handler here to stream per-step status
            # while the run is in flight, instead of only rendering at the end.
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(None, run_workflow, loaded)
        except WorkflowError as exc:
            # Hard failure: a step failed without continue_on_error. Show it
            # prominently rather than letting the worker thread die silently.
            results_view.controls = [
                ft.Text("Run failed", color=_ERROR_COLOR, size=16),
                ft.Text(str(exc), color=_ERROR_COLOR),
            ]
        else:
            results_view.controls = _build_result_controls(results)
        finally:
            _set_busy(False)
            page.update()

    open_button.on_click = on_open
    run_button.on_click = on_run

    # TODO(export): a "Save run log" button would hook in here, reusing
    # conductor.__main__._build_run_log over the last results.
    # TODO(edit): in-app workflow editing is out of v1 scope (load-and-run only).
    page.add(
        ft.Column(
            [
                title,
                subtitle,
                ft.Row([open_button, run_button, progress, running_text]),
                ft.Divider(),
                plan_view,
                ft.Divider(),
                results_view,
            ],
            expand=True,
        )
    )


def _build_plan_controls(workflow: Workflow) -> list[ft.Control]:
    """Build the read-only plan controls from presenter view objects."""
    controls: list[ft.Control] = [ft.Text(presenter.plan_title(workflow), size=16)]
    for row in presenter.plan_rows(workflow):
        controls.append(ft.Text(row.headline))
        controls.append(ft.Text(f"    prompt: {row.prompt}", italic=True))
    return controls


def _build_result_controls(results: list[StepResult]) -> list[ft.Control]:
    """Build the results controls (one block per step plus an overall line)."""
    controls: list[ft.Control] = []
    for row in presenter.result_rows(results):
        controls.append(
            ft.Text(row.headline, color=_ERROR_COLOR if row.is_error else None)
        )
        if row.output:
            controls.append(ft.Text(f"    output: {row.output}"))
        if row.error:
            controls.append(ft.Text(f"    error: {row.error}", color=_ERROR_COLOR))
    controls.append(
        ft.Text(
            presenter.overall_summary(results),
            size=16,
            color=_ERROR_COLOR if presenter.overall_is_error(results) else _OK_COLOR,
        )
    )
    return controls
