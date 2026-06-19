"""Conductor desktop UI — a thin Flet layer over the headless engine.

Additive only: nothing here is imported by the core engine, and the engine
itself stays stdlib-only. Display logic lives in :mod:`conductor.ui.presenter`
(pure, unit-tested); :mod:`conductor.ui.app` is the Flet shell. Run it with
``python -m conductor.ui`` after ``pip install -r requirements-ui.txt``.
"""

from __future__ import annotations
