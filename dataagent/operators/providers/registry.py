from __future__ import annotations

from collections.abc import Iterable

from .protocol import OperatorProvider, ProviderHealth, ProviderOperatorDescriptor


class ProviderRegistry:
    def __init__(self, providers: Iterable[OperatorProvider] = ()) -> None:
        self._providers: dict[str, OperatorProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: OperatorProvider) -> None:
        if provider.provider_id in self._providers:
            raise ValueError(f"Provider already registered: {provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> OperatorProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise KeyError(f"Provider not found: {provider_id}") from exc

    def discover(self, provider_id: str | None = None) -> list[ProviderOperatorDescriptor]:
        providers = [self.get(provider_id)] if provider_id else list(self._providers.values())
        descriptors: list[ProviderOperatorDescriptor] = []
        for provider in providers:
            if provider.health().status == "unavailable":
                continue
            descriptors.extend(provider.discover())
        return sorted(
            descriptors,
            key=lambda item: (item.provider_id, item.provider_operator_ref),
        )

    def health(self) -> list[ProviderHealth]:
        return [self._providers[key].health() for key in sorted(self._providers)]


__all__ = ["ProviderRegistry"]
