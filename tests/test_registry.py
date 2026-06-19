"""Tests for the adapter registry."""

from __future__ import annotations

import pytest

from conductor.adapters import AdapterError, EchoAdapter
from conductor.registry import ADAPTERS, adapter_names, create_adapter


def test_known_name_resolves_to_instance():
    assert isinstance(create_adapter("EchoAdapter"), EchoAdapter)


def test_unknown_name_raises_clear_error():
    with pytest.raises(AdapterError) as exc_info:
        create_adapter("DoesNotExist")
    message = str(exc_info.value)
    assert "unknown adapter 'DoesNotExist'" in message
    assert "known adapters" in message


def test_adapter_names_lists_all_builtins():
    names = adapter_names()
    assert names == sorted(names)
    for expected in (
        "AntigravityEditorAdapter",
        "ClaudeCodeAdapter",
        "EchoAdapter",
        "VSCodeAdapter",
    ):
        assert expected in names


def test_registry_maps_names_to_classes():
    assert ADAPTERS["EchoAdapter"] is EchoAdapter


def test_create_adapter_accepts_custom_registry():
    custom = {"EchoAdapter": EchoAdapter}
    assert isinstance(create_adapter("EchoAdapter", custom), EchoAdapter)
    with pytest.raises(AdapterError):
        create_adapter("ClaudeCodeAdapter", custom)
