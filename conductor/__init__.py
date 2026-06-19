"""Conductor — a minimal engine for chaining AI tool adapters.

This package is the v1 engine skeleton: no UI, no GUI-automation. It defines
an adapter interface, a couple of concrete adapters, a JSON-backed workflow
model, and a runner that pipes each step's output into the next.
"""

__version__ = "0.1.0"
