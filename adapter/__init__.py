"""Adapter factory and shared exports."""

from __future__ import annotations

from ..config import PluginSettings, get_settings
from ..db import get_active_tour_info
from .base import EbayAdapter
from .live import EbayApiError, LiveEbayAdapter
from .mock import MockEbayAdapter

_ACTIVE_ADAPTER_OVERRIDE: EbayAdapter | None = None


def set_adapter_override(adapter: EbayAdapter | None) -> None:
    """Inject a deterministic EbayAdapter instance (e.g. MockEbayAdapter in tests)."""
    global _ACTIVE_ADAPTER_OVERRIDE
    _ACTIVE_ADAPTER_OVERRIDE = adapter


def get_adapter(settings: PluginSettings | None = None) -> EbayAdapter:
    """Return the EbayAdapter for this call: the sample adapter during a tour, else as configured."""
    if _ACTIVE_ADAPTER_OVERRIDE is not None:
        return _ACTIVE_ADAPTER_OVERRIDE
    cfg = settings or get_settings()
    tour = get_active_tour_info(cfg.plugin_name)
    if tour:
        # A sample tour reads and writes tour.db. It must never reach the seller's live store,
        # whichever surface (chat tool, Seller Desk, dashboard) makes the call.
        return MockEbayAdapter(persona="lite" if tour.get("persona") == "lite" else "pro")
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
