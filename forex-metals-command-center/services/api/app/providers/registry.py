"""Provider registry. Selects the configured independent market-data provider."""

from __future__ import annotations

from collections.abc import Callable

from app.providers.base import MarketDataProvider

ProviderFactory = Callable[[], MarketDataProvider]

# TradingView is a chart renderer only (spec: "TRADINGVIEW: Renderer only; never market-data source").
FORBIDDEN_PROVIDER_MARKERS = ("tradingview", "trading_view", "trading-view")


class ForbiddenProviderError(ValueError):
    pass


class UnknownProviderError(KeyError):
    pass


def _check_allowed(name: str) -> None:
    lowered = name.lower().replace(" ", "")
    if any(marker in lowered for marker in FORBIDDEN_PROVIDER_MARKERS):
        raise ForbiddenProviderError(f"'{name}' may not be registered as a market-data provider")


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, name: str, factory: ProviderFactory) -> None:
        _check_allowed(name)
        key = name.lower()
        if key in self._factories:
            raise ValueError(f"provider '{name}' already registered")
        self._factories[key] = factory

    def names(self) -> list[str]:
        return sorted(self._factories)

    def create(self, name: str) -> MarketDataProvider:
        _check_allowed(name)
        try:
            factory = self._factories[name.lower()]
        except KeyError as exc:
            raise UnknownProviderError(name) from exc
        provider = factory()
        _check_allowed(provider.name)
        if not isinstance(provider, MarketDataProvider):
            raise TypeError(f"provider '{name}' does not implement MarketDataProvider")
        return provider


def default_registry() -> ProviderRegistry:
    from app.providers.fixture import SyntheticFixtureProvider
    from app.providers.unconfigured import UnconfiguredProvider

    registry = ProviderRegistry()
    registry.register(UnconfiguredProvider.name, UnconfiguredProvider)
    registry.register(SyntheticFixtureProvider.name, SyntheticFixtureProvider)
    return registry
