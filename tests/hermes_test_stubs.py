"""Minimal Hermes inventory module shim for tests outside a Hermes checkout."""
from __future__ import annotations

import sys
import types


def ensure_inventory_module() -> types.ModuleType:
    """Make patch('hermes_cli.inventory....') work without Hermes installed."""
    package = sys.modules.get("hermes_cli")
    if package is None:
        package = types.ModuleType("hermes_cli")
        package.__path__ = []  # type: ignore[attr-defined]
        sys.modules["hermes_cli"] = package
    inventory = sys.modules.get("hermes_cli.inventory")
    if inventory is None:
        inventory = types.ModuleType("hermes_cli.inventory")
        setattr(inventory, "load_picker_context", lambda: None)
        setattr(inventory, "build_models_payload", lambda *_args, **_kwargs: {})
        sys.modules["hermes_cli.inventory"] = inventory
    setattr(package, "inventory", inventory)
    return inventory
