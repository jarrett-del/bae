"""Task 5 verification: Progressive Lite-to-Pro enrichment, check_fn tool gating,
bundled SKILL.md registration, SellerAccept offer filtering, and sorted bin pick-lists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from hermes_ebay_ops import SKILL_PATH, is_pro_ops_enabled, register
from hermes_ebay_ops.adapter import MockEbayAdapter, set_adapter_override
from hermes_ebay_ops.config import configure_settings
from hermes_ebay_ops.db import get_db, upsert_item
from hermes_ebay_ops.models import ConditionEnum, HRM_SKU_PATTERN, ItemStatus, SourceMode
from hermes_ebay_ops.tools.phase1_mvp import handle_confirm_action, handle_stage_listing
from hermes_ebay_ops.tools.phase2_ops import handle_lookup_bin, handle_triage_offers

REPO_ROOT = Path(__file__).resolve().parent.parent


class RecordingContext:
    def __init__(self) -> None:
        self.tools: dict[str, dict[str, Any]] = {}
        self.skills: dict[str, Path] = {}

    def register_tool(
        self,
        *,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: Any,
        check_fn: Any | None = None,
    ) -> None:
        self.tools[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            "check_fn": check_fn,
        }

    def register_skill(self, name: str, path: Path, description: str = "", frontmatter: Any = None) -> None:
        self.skills[name] = Path(path)


def test_manifest_alignment_skill_bundle_and_initial_lite_gating() -> None:
    manifest = yaml.safe_load((REPO_ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    ctx = RecordingContext()
    register(ctx)

    # Capabilities declared in plugin.yaml match register(ctx) 1-to-1
    assert set(ctx.tools.keys()) == set(manifest["provides_tools"])
    assert set(ctx.skills.keys()) == {
        "ebay-seller-ops",
        "ebay-apparel-footwear",
        "ebay-motors-fitment",
        "ebay-vintage-collectibles",
        "ebay-cards-coins-media",
    }
    for skill_name, skill_file in ctx.skills.items():
        assert skill_file.exists(), f"Missing SKILL.md for {skill_name}"
    assert manifest["oauth"][0]["id"] == "ebay"
    assert SKILL_PATH.exists()
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    assert "SellerAccept" in skill_text
    assert "ebay_confirm_action" in skill_text

    # Phase 1 tools are ungated; Phase 2 tools are gated behind check_fn=is_pro_ops_enabled
    for tname in (
        "ebay_stage_listing",
        "ebay_audit_active_listing",
        "ebay_check_comps",
        "ebay_confirm_action",
        "ebay_export_draft_csv",
    ):
        assert ctx.tools[tname]["check_fn"] is None

    for pro_tool in (
        "ebay_triage_offers",
        "ebay_lookup_bin",
        "ebay_import_migration_csv",
        "ebay_diagnose_stale",
    ):
        gate_fn = ctx.tools[pro_tool]["check_fn"]
        assert gate_fn is not None
        assert gate_fn() is False  # Casual C2C profile starts with Pro tools hidden


def test_progressive_c2c_to_pro_promotion_unlocks_check_fn(tmp_path: Path) -> None:
    mock_adapter = MockEbayAdapter(persona="lite")
    set_adapter_override(mock_adapter)

    img = tmp_path / "item.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0PHOTO\xff\xd9")

    # Step 1: Casual C2C intake with zero SKU, COGS, or bin -> stays Lite, auto-mints HRM SKU
    lite_res = json.loads(
        handle_stage_listing(
            {
                "media_paths": [str(img)],
                "seller_notes": "Patagonia fleece pullover mens Large, pit to pit 23 in, length 28 in.",
                "price_cents": 6000,
            }
        )
    )
    assert lite_res["ok"] is True
    assert lite_res["mode"] == "lite"
    assert HRM_SKU_PATTERN.match(lite_res["sku"])
    assert lite_res["economics"]["net_profit_cents"] is None
    assert "Estimated Net Payout" in lite_res["preview_card_md"]
    assert "Pro Unit Economics" not in lite_res["preview_card_md"]
    assert is_pro_ops_enabled() is False

    # Step 2: Reseller supplies cogs_cents + bin_code -> automatically promotes profile to Pro!
    pro_res = json.loads(
        handle_stage_listing(
            {
                "media_paths": [str(img)],
                "seller_notes": "Levi's 501 selvedge jeans mens 32x30, light cuff wear.",
                "price_cents": 7500,
                "cogs_cents": 1800,
                "bin_code": "a-02-04",
                "floor_price_cents": 5800,
            }
        )
    )
    assert pro_res["ok"] is True
    assert pro_res["mode"] == "pro"
    assert pro_res["economics"]["net_profit_cents"] is not None
    assert pro_res["economics"]["margin_pct"] > 0
    assert "Pro Unit Economics" in pro_res["preview_card_md"]
    assert is_pro_ops_enabled() is True

    # Config mode override ("lite" vs "pro") also respected deterministically
    configure_settings({"mode": "lite", "adapter_mode": "mock"})
    assert is_pro_ops_enabled() is False
    configure_settings({"mode": "pro", "adapter_mode": "mock"})
    assert is_pro_ops_enabled() is True


def test_pro_triage_offers_skips_seller_accept_and_stages_accept_or_counter() -> None:
    mock_adapter = MockEbayAdapter(persona="pro")
    set_adapter_override(mock_adapter)

    # Seed Pro item matching mock Best Offers (6200 >= floor 5800 -> Accept; 3800 < floor 5800 -> Counter)
    conn = get_db()
    try:
        upsert_item(
            conn,
            sku="PRO-LEVI-501-32X30",
            ebay_item_id="110599005678",
            offer_id="OFF-9001",
            source_mode=SourceMode.AGENT_CAPTURE,
            status=ItemStatus.ACTIVE,
            title="Levi's 501 Original Selvedge Jeans 32x30",
            category_id="11483",
            condition_enum=ConditionEnum.USED_EXCELLENT,
            list_price_cents=7500,
            shipping_est_cents=795,
            cogs_cents=1800,
            bin_code="B-10-03",
            floor_price_cents=5800,
        )
    finally:
        conn.close()

    triage_res = json.loads(handle_triage_offers({}, session_id="sess-pro-offers"))
    assert triage_res["ok"] is True

    # BO-SELLERACCEPT-103 must NEVER appear in actionable_offers; must be in awaiting_buyer_payment
    actionable_by_id = {o["best_offer_id"]: o for o in triage_res["actionable_offers"]}
    awaiting_ids = {o["best_offer_id"] for o in triage_res["awaiting_buyer_payment"]}

    assert "BO-SELLERACCEPT-103" not in actionable_by_id
    assert "BO-SELLERACCEPT-103" in awaiting_ids

    assert actionable_by_id["BO-ACTIVE-101"]["recommended_action"] == "Accept"
    assert actionable_by_id["BO-LOW-102"]["recommended_action"] == "Counter"
    assert actionable_by_id["BO-LOW-102"]["counter_price_cents"] >= 5800
    assert mock_adapter.write_calls == []

    # Confirming the Accept action_token executes RespondToBestOffer -> SellerAccept
    confirm_res = json.loads(
        handle_confirm_action(
            {
                "action_token": actionable_by_id["BO-ACTIVE-101"]["action_token"],
                "decision": "APPROVE",
            }
        )
    )
    assert confirm_res["ok"] is True
    assert confirm_res["resulting_status"] == "SellerAccept"
    assert len(mock_adapter.write_calls) == 1
    assert mock_adapter.write_calls[0][0] == "respond_to_offer"


def test_pro_lookup_bin_returns_naturally_sorted_warehouse_pick_list() -> None:
    mock_adapter = MockEbayAdapter(persona="pro")
    set_adapter_override(mock_adapter)

    conn = get_db()
    try:
        upsert_item(
            conn,
            sku="PRO-LEVI-501-32X30",
            ebay_item_id="110599005678",
            source_mode=SourceMode.AGENT_CAPTURE,
            status=ItemStatus.SOLD,
            title="Levi's 501 Jeans",
            category_id="11483",
            condition_enum=ConditionEnum.USED_EXCELLENT,
            list_price_cents=7500,
            bin_code="B-10-03",
        )
        upsert_item(
            conn,
            sku="PRO-SHOE-NB990",
            ebay_item_id="110599009901",
            source_mode=SourceMode.AGENT_CAPTURE,
            status=ItemStatus.SOLD,
            title="New Balance 990v5",
            category_id="15709",
            condition_enum=ConditionEnum.USED_GOOD,
            list_price_cents=11500,
            bin_code="A-02-01",
        )
    finally:
        conn.close()

    pick_res = json.loads(handle_lookup_bin({"limit": 25}))
    assert pick_res["ok"] is True
    assert pick_res["count"] == 3
    bins_in_order = [row["bin_code"] for row in pick_res["pick_list"]]
    assert bins_in_order == ["A-02-01", "B-10-03", "UNASSIGNED"]
