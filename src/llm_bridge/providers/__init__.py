"""Provider registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_bridge.providers.base import BaseProvider

_registry: dict[str, BaseProvider] = {}


def register_provider(name: str, provider: BaseProvider) -> None:
    _registry[name] = provider


def get_provider(name: str) -> BaseProvider | None:
    return _registry.get(name)


def get_all_providers() -> dict[str, BaseProvider]:
    return dict(_registry)
