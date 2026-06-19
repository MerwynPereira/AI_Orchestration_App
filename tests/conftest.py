"""Shared pytest fixtures.

The CLI configures the root logger via ``logging.basicConfig``. Without cleanup
that handler leaks between tests and can point at a closed capture buffer, so we
strip root handlers after every test.
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _reset_root_logging():
    """Remove any root logging handlers a test installed, after it runs."""
    yield
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)
