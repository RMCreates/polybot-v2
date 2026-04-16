from signals.base import SignalProvider

_REGISTRY: dict[str, "SignalProvider"] = {}

def register(provider: "SignalProvider") -> None:
    _REGISTRY[provider.name] = provider

def get(name: str) -> "SignalProvider":
    if name not in _REGISTRY:
        raise KeyError(
            f"No signal provider registered under '{name}'. "
            f"Available: {list(_REGISTRY.keys())}"
        )
    return _REGISTRY[name]

def list_providers() -> list[str]:
    return list(_REGISTRY.keys())
