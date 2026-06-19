"""Entry point: ``python -m conductor.ui`` opens the desktop window.

Requires the UI dependency (``pip install -r requirements-ui.txt``); ``flet`` is
imported here, not by the engine.
"""

from __future__ import annotations

import flet as ft

from .app import main

if __name__ == "__main__":
    ft.run(main)
