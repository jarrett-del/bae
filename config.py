"""Runtime configuration for hermes-ebay-ops.

Zero raw credential access: this module never reads environment variables,
.env files, or token stores. All OAuth credentials remain inside Hermes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PLUGIN_NAME = "hermes-ebay-ops"
OAUTH_PROVIDER_ID = "ebay"


class PluginSettings(BaseModel):
    """Validated non-secret operational settings for hermes-ebay-ops."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    plugin_name: str = PLUGIN_NAME
    oauth_provider_id: str = OAUTH_PROVIDER_ID
    mode: Literal["auto", "lite", "pro"] = "auto"
    adapter_mode: Literal["live", "mock"] = "live"
    mock_persona: Literal["lite", "pro"] = "lite"
    marketplace_id: str = "EBAY_US"
    default_estimated_fee_bps: int = Field(default=1360, ge=0, le=10000)
    per_order_fixed_fee_cents: int = Field(default=40, ge=0, le=5000)
    action_token_ttl_seconds: int = Field(default=900, gt=0, le=86400)
    api_base_url: str = "https://api.ebay.com"
    media_base_url: str = "https://apiz.ebay.com"
    trading_xml_url: str = "https://api.ebay.com/ws/api.dll"
    trading_compat_level: str = "1489"
    trading_site_id: str = "0"


_ACTIVE_SETTINGS: PluginSettings = PluginSettings()


def get_settings() -> PluginSettings:
    """Return the active plugin settings instance."""
    return _ACTIVE_SETTINGS


def configure_settings(overrides: Mapping[str, Any] | PluginSettings | None = None) -> PluginSettings:
    """Update active plugin settings from Hermes plugin configuration or test overrides."""
    global _ACTIVE_SETTINGS
    if overrides is None:
        _ACTIVE_SETTINGS = PluginSettings()
    elif isinstance(overrides, PluginSettings):
        _ACTIVE_SETTINGS = overrides
    else:
        _ACTIVE_SETTINGS = PluginSettings.model_validate(dict(overrides))
    return _ACTIVE_SETTINGS
