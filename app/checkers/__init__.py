import importlib
import pkgutil

from .base import REGISTRY, Checker, Field, all_types, get, register, to_text  # noqa: F401

# Auto-discover every checker module in this package.
for _m in pkgutil.iter_modules(__path__):
    if _m.name != "base":
        importlib.import_module(f"{__name__}.{_m.name}")
