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
    SCHEMA_EXPORT_DRAFT_CSV,
    SCHEMA_STAGE_LISTING,
    handle_audit_active_listing,
    handle_check_comps,
    handle_confirm_action,
    handle_export_draft_csv,
    handle_stage_listing,
)
from .tools.phase2_ops import (
    SCHEMA_DIAGNOSE_STALE,
    SCHEMA_IMPORT_MIGRATION_CSV,
    SCHEMA_LOOKUP_BIN,
    SCHEMA_TRIAGE_OFFERS,
    handle_diagnose_stale,
    handle_import_migration_csv,
    handle_lookup_bin,
    handle_triage_offers,
)

TOOLSET_NAME = "ebay_ops"
SKILLS_DIR = Path(__file__).resolve().parent / "skills"
SKILL_PATH = SKILLS_DIR / "ebay-seller-ops" / "SKILL.md"
SKILL_DESCRIPTION = (
    "Multimodal listing intake, anti-INAD QA etiquette, 2026 unit economics (PLG + 139-DIM), and progressive C2C-to-Pro store workflows."
)
CATEGORY_SKILLS: tuple[tuple[str, Path, str], ...] = (
    (
        "ebay-apparel-footwear",
        SKILLS_DIR / "ebay-apparel-footwear" / "SKILL.md",
        "Apparel & footwear playbook: Aug 2026 Size Standardization, 2990/3000/3010 fashion conditions, ruler measurements, and $200-$500 Ship With eBay guard.",
    ),
    (
        "ebay-motors-fitment",
        SKILLS_DIR / "ebay-motors-fitment" / "SKILL.md",
        "eBay Motors Parts & Accessories playbook: OEM MPN front-loading, YMM fitment tables (replacing myFitment), and mandatory 30-day free returns > $10.",
    ),
    (
        "ebay-vintage-collectibles",
        SKILLS_DIR / "ebay-vintage-collectibles" / "SKILL.md",
        "Vintage & Collectibles playbook: Catalog-Number Defense keeping mold/box numbers out of MPN/Model, search_by_image comps, and condition disclosures.",
    ),
    (
        "ebay-cards-coins-media",
        SKILLS_DIR / "ebay-cards-coins-media" / "SKILL.md",
        "Trading Cards, Coins & Media playbook: PSA/BGS/SGC/PCGS slab verification, May 2026 mandatory coin condition grading, and eBay Standard Envelope rules.",
    ),
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
    ctx.register_tool(
        name="ebay_export_draft_csv",
        toolset=TOOLSET_NAME,
        schema=SCHEMA_EXPORT_DRAFT_CSV,
        handler=handle_export_draft_csv,
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
    ctx.register_tool(
        name="ebay_import_migration_csv",
        toolset=TOOLSET_NAME,
        schema=SCHEMA_IMPORT_MIGRATION_CSV,
        handler=handle_import_migration_csv,
        check_fn=is_pro_ops_enabled,
    )
    ctx.register_tool(
        name="ebay_diagnose_stale",
        toolset=TOOLSET_NAME,
        schema=SCHEMA_DIAGNOSE_STALE,
        handler=handle_diagnose_stale,
        check_fn=is_pro_ops_enabled,
    )

    # Bundled Multimodal Seller Etiquette + 4 Specialized 2026 Category Playbook Skills
    ctx.register_skill("ebay-seller-ops", SKILL_PATH, description=SKILL_DESCRIPTION)
    for skill_name, skill_file, skill_desc in CATEGORY_SKILLS:
        ctx.register_skill(skill_name, skill_file, description=skill_desc)
