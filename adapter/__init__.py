"""Adapter factory and shared exports."""

from __future__ import annotations

from ..config import PluginSettings, get_settings
from .base import EbayAdapter
from .live import EbayApiError, LiveEbayAdapter
from .mock import MockEbayAdapter

_ACTIVE_ADAPTER_OVERRIDE: EbayAdapter | None = None


def set_adapter_override(adapter: EbayAdapter | None) -> None:
    """Inject a deterministic EbayAdapter instance (e.g. MockEbayAdapter in tests)."""
    global _ACTIVE_ADAPTER_OVERRIDE
    _ACTIVE_ADAPTER_OVERRIDE = adapter


def get_adapter(settings: PluginSettings | None = None) -> EbayAdapter:
    """Return the configured EbayAdapter implementation."""
    if _ACTIVE_ADAPTER_OVERRIDE is not None:
        return _ACTIVE_ADAPTER_OVERRIDE
    cfg = settings or get_settings()
    if cfg.adapter_mode == "mock":
        return MockEbayAdapter(persona=cfg.mock_persona)
    return LiveEbayAdapter(settings=cfg)


__all__ = [
    "EbayAdapter",
    "EbayApiError",
    "LiveEbayAdapter",
    "MockEbayAdapter",
    "get_adapter",
    "set_adapter_override",
]
