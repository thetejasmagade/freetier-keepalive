"""Plugin base. To add a new service type, create a module in this package:

    from .base import Checker, Field, register

    @register
    class MyChecker(Checker):
        type = "mytype"
        label = "My service"
        fields = [Field("url", "URL", required=True)]

        def check(self, cfg, timeout):
            ...  # raise an exception on failure
            return "what the service replied"   # optional, shown on "Test connection"

It is discovered automatically and appears in the "Add service" form.
"""
import json
from dataclasses import dataclass, field


@dataclass
class Field:
    name: str
    label: str
    secret: bool = False        # stored encrypted, never shown again
    required: bool = False
    default: str = ""
    placeholder: str = ""
    kind: str = "text"          # text | textarea | select
    choices: list = field(default_factory=list)
    help: str = ""


class Checker:
    type: str = ""
    label: str = ""
    fields: list = []

    def check(self, cfg: dict, timeout: int):  # pragma: no cover - interface
        """Raise on failure. May return a short text (or any value) describing the reply."""
        raise NotImplementedError


def to_text(value) -> str:
    """Compact, readable text for any reply value (dicts, lists, bytes, ...)."""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, ensure_ascii=False)


def format_rows(columns, rows, limit: int = 5) -> str:
    """Query result as text: '1 row: {"col": value}' with at most `limit` rows shown."""
    columns = list(columns or [])
    rows = list(rows or [])
    n = len(rows)
    head = f"{n} row{'s' if n != 1 else ''}"
    if not n:
        return head
    lines = []
    for r in rows[:limit]:
        if isinstance(r, dict):
            lines.append(to_text(r))
        else:
            lines.append(to_text(dict(zip(columns, r))) if columns else to_text(list(r)))
    if n > limit:
        lines.append(f"... {n - limit} more")
    return head + ":\n" + "\n".join(lines)


REGISTRY: dict = {}


def register(cls):
    REGISTRY[cls.type] = cls
    return cls


def get(type_: str):
    return REGISTRY.get(type_)


def all_types() -> list:
    return sorted(REGISTRY.values(), key=lambda c: c.label)
