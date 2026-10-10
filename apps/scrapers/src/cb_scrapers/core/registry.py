"""Adapters register themselves here and `cb` finds them by name."""

from importlib import import_module

from .adapter import Adapter

_ADAPTERS: dict[str, type[Adapter]] = {}


def register[A: type[Adapter]](cls: A) -> A:
    if cls.name in _ADAPTERS:
        raise ValueError(f"adapter {cls.name!r} is registered twice")
    _ADAPTERS[cls.name] = cls
    return cls


def all_adapters() -> dict[str, type[Adapter]]:
    import_module("cb_scrapers.adapters")  # the package imports every adapter module, which registers it
    return dict(sorted(_ADAPTERS.items()))
