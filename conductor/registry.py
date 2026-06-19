"""Adapter registry: maps adapter names to their classes.

The registry is the single place that knows which adapters exist. The runner
and the workflow loader look adapters up here by the name used in workflow JSON
(the class name, e.g. ``"EchoAdapter"``).

To add an adapter: implement it in :mod:`conductor.adapters`, then add one line
to :data:`ADAPTERS` below.
"""

from __future__ import annotations

from .adapters import (
    Adapter,
    AdapterError,
    AntigravityEditorAdapter,
    ClaudeCodeAdapter,
    ClaudeDesktopAdapter,
    EchoAdapter,
    VSCodeAdapter,
)

# Adapter name -> adapter class. Add new adapters here (one line each).
ADAPTERS: dict[str, type[Adapter]] = {
    "EchoAdapter": EchoAdapter,
    "ClaudeCodeAdapter": ClaudeCodeAdapter,
    "VSCodeAdapter": VSCodeAdapter,
    "AntigravityEditorAdapter": AntigravityEditorAdapter,
    "ClaudeDesktopAdapter": ClaudeDesktopAdapter,
}


def adapter_names(registry: dict[str, type[Adapter]] | None = None) -> list[str]:
    """Return the registered adapter names, sorted.

    Args:
        registry: Registry to read. Defaults to the built-in :data:`ADAPTERS`.

    Returns:
        Sorted list of adapter names.
    """
    return sorted(registry if registry is not None else ADAPTERS)


def create_adapter(
    name: str,
    registry: dict[str, type[Adapter]] | None = None,
) -> Adapter:
    """Instantiate the adapter registered under ``name``.

    Args:
        name: Adapter name as used in workflow JSON.
        registry: Registry to look up in. Defaults to :data:`ADAPTERS`.

    Returns:
        A new adapter instance.

    Raises:
        AdapterError: If ``name`` is not registered.
    """
    active = registry if registry is not None else ADAPTERS
    try:
        adapter_cls = active[name]
    except KeyError:
        known = ", ".join(adapter_names(active)) or "(none)"
        raise AdapterError(
            f"unknown adapter {name!r}; known adapters: {known}"
        ) from None
    return adapter_cls()
