"""hermes-ebay-ops plugin package entrypoint and register(ctx) wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import PLUGIN_NAME, PluginSettings, configure_settings, get_settings
from .db import get_db, is_pro_ops_flag_set
from .tools.phase1_mvp import (
    SCHEMA_AUDIT_ACTIVE_LISTING,
    SCHEMA_CHECK_COMPS,
    SCHEMA_CONFIRM_ACTION,
    SCHEMA_STAGE_LISTING,
    handle_audit_active_listing,
    handle_check_comps,
    handle_confirm_action,
    handle_stage_listing,
)
from .tools.phase2_ops import (
    SCHEMA_LOOKUP_BIN,
    SCHEMA_TRIAGE_OFFERS,
    handle_lookup_bin,
    handle_triage_offers,
)

TOOLSET_NAME = "ebay_ops"
SKILL_PATH = Path(__file__).resolve().parent / "skills" / "ebay-seller-ops" / "SKILL.md"
SKILL_DESCRIPTION = (
    "Multimodal listing intake, anti-INAD QA etiquette, unit economics, and progressive C2C-to-Pro store workflows."
)


def is_pro_ops_enabled() -> bool:
    """Conditional tool gate returning True when Pro warehouse/offer tools are unlocked."""
    settings = get_settings()
    if settings.mode == "pro":
        return True
    if settings.mode == "lite":
        return False
    conn = get_db(settings.plugin_name)
    try:
        return is_pro_ops_flag_set(conn)
    finally:
        conn.close()


def _load_ctx_settings(ctx: Any) -> None:
    """Populate active PluginSettings via Hermes's public ctx.get_config surface."""
    if not hasattr(ctx, "get_config"):
        return
    defaults = PluginSettings()
    overrides: dict[str, Any] = {}
    for field_name in (
        "mode",
        "adapter_mode",
        "mock_persona",
        "marketplace_id",
        "default_estimated_fee_bps",
        "per_order_fixed_fee_cents",
        "action_token_ttl_seconds",
        "api_base_url",
        "media_base_url",
    ):
        default_val = getattr(defaults, field_name)
        val = ctx.get_config(field_name, default_val)
        if val is not None and val != default_val:
            overrides[field_name] = val
    if overrides:
        merged = defaults.model_dump()
        merged.update(overrides)
        configure_settings(merged)


def register(ctx: Any) -> None:
    """Register hermes-ebay-ops tools and skills strictly via Hermes public PluginContext."""
    _load_ctx_settings(ctx)

    # Phase 1 Core Tools (always active when plugin is enabled)
    ctx.register_tool(
        name="ebay_stage_listing",
        toolset=TOOLSET_NAME,
        schema=SCHEMA_STAGE_LISTING,
        handler=handle_stage_listing,
    )
    ctx.register_tool(
        name="ebay_audit_active_listing",
        toolset=TOOLSET_NAME,
        schema=SCHEMA_AUDIT_ACTIVE_LISTING,
        handler=handle_audit_active_listing,
    )
    ctx.register_tool(
        name="ebay_check_comps",
        toolset=TOOLSET_NAME,
        schema=SCHEMA_CHECK_COMPS,
        handler=handle_check_comps,
    )
    ctx.register_tool(
        name="ebay_confirm_action",
        toolset=TOOLSET_NAME,
        schema=SCHEMA_CONFIRM_ACTION,
        handler=handle_confirm_action,
    )

    # Phase 2 Pro Reseller Tools (gated via check_fn=is_pro_ops_enabled)
    ctx.register_tool(
        name="ebay_triage_offers",
        toolset=TOOLSET_NAME,
        schema=SCHEMA_TRIAGE_OFFERS,
        handler=handle_triage_offers,
        check_fn=is_pro_ops_enabled,
    )
    ctx.register_tool(
        name="ebay_lookup_bin",
        toolset=TOOLSET_NAME,
        schema=SCHEMA_LOOKUP_BIN,
        handler=handle_lookup_bin,
        check_fn=is_pro_ops_enabled,
    )

    # Bundled Multimodal Seller Etiquette Skill
    ctx.register_skill("ebay-seller-ops", SKILL_PATH, description=SKILL_DESCRIPTION)
