"""Plugin base. To add a new service type, create a module in this package:

    from .base import Checker, Field, register

    @register
    class MyChecker(Checker):
        type = "mytype"
        label = "My service"
        fields = [Field("url", "URL", required=True)]

        def check(self, cfg, timeout):
            ...  # raise an exception on failure

It is discovered automatically and appears in the "Add service" form.
"""
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

    def check(self, cfg: dict, timeout: int) -> None:  # pragma: no cover - interface
        raise NotImplementedError


REGISTRY: dict = {}


def register(cls):
    REGISTRY[cls.type] = cls
    return cls


def get(type_: str):
    return REGISTRY.get(type_)


def all_types() -> list:
    return sorted(REGISTRY.values(), key=lambda c: c.label)
