"""Sample-data tours for the Vendere (hermes-ebay-ops) Dashboard & Seller Studio Webapp.

Seeds an isolated `tour.db` inside `plugin_data_dir("hermes-ebay-ops")` so sellers can
explore both Lite C2C and Pro Warehouse Store workflows without touching their real
`data.db` inventory ledger.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from plugins.plugin_storage import plugin_data_dir

from .cache_store import put_cached_category_aspects, upsert_haul_batch
from .config import PLUGIN_NAME, get_settings
from .db import (
    append_item_event,
    get_db,
    get_or_create_seller_profile,
    set_active_tour_info,
    stage_pending_action,
    update_seller_capabilities,
    upsert_item,
)
from .models import (
    AccountCapabilities,
    ActionType,
    BestOfferLifecycleStatus,
    ConditionEnum,
    EventActor,
    ItemStatus,
    OfferDecisionAction,
    RespondOfferPayload,
    ReviseListingPayload,
    SourceMode,
    StagedListingPayload,
)

TOURS: dict[str, dict[str, Any]] = {
    "closet": {
        "id": "closet",
        "title": "Weekend Closet & Tech Clearout",
        "subtitle": "Casual C2C Seller · Lite Mode (No SKU, COGS, or Bin Clutter)",
        "persona": "lite",
    },
    "vault": {
        "id": "vault",
        "title": "Pacific Vintage & Camera Vault",
        "subtitle": "Pro Reseller Store · Unit Economics, Offer Triage & Bin Pick-List",
        "persona": "pro",
    },
}


def get_tour_catalog() -> list[dict[str, Any]]:
    return list(TOURS.values())


def get_tour_demo_extras(tour_id: str) -> dict[str, Any]:
    """Return deterministic mock Best Offers, Paid Orders, and Comp Bands for active tours."""
    now = datetime.now(timezone.utc)
    if tour_id == "closet":
        return {
            "offers": [],
            "paid_orders": [
                {
                    "order_id": "19-11842-77410",
                    "ebay_item_id": "110588300103",
                    "sku": "HRM-20261110-4A9C",
                    "title": "Apple AirPods Pro 2nd Gen MagSafe Case (USB-C) Complete",
                    "quantity": 1,
                    "total_cents": 15900,
                    "buyer_username": "westcoast_audio",
                    "paid_at": (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            ],
            "comp_bands": {
                "HRM-20261112-8F21": {"p25_cents": 5400, "median_cents": 6500, "p75_cents": 7400, "sample_count": 14},
                "HRM-20261111-2D19": {"p25_cents": 3800, "median_cents": 4600, "p75_cents": 5500, "sample_count": 18},
                "HRM-20261110-4A9C": {"p25_cents": 14500, "median_cents": 15900, "p75_cents": 17200, "sample_count": 22},
                "HRM-20261109-9E04": {"p25_cents": 22500, "median_cents": 24500, "p75_cents": 26900, "sample_count": 19},
            },
        }

    # Pro Vault tour: rich Best Offers (including March 2026 SellerAccept!) + 4 warehouse orders
    return {
        "offers": [
            {
                "best_offer_id": "BO-99401",
                "ebay_item_id": "220914800202",
                "sku": "VLT-FILSON-202",
                "buyer_username": "pnw_heritage",
                "price_cents": 24500,
                "status": BestOfferLifecycleStatus.ACTIVE.value,
                "quantity": 1,
                "buyer_message": "Can pay immediately if $245 works — shipping to Seattle!",
                "expiration_time": (now + timedelta(hours=22)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            {
                "best_offer_id": "BO-99402",
                "ebay_item_id": "220914800203",
                "sku": "VLT-CONTAX-203",
                "buyer_username": "analog_tokyo_us",
                "price_cents": 86000,
                "status": BestOfferLifecycleStatus.ACTIVE.value,
                "quantity": 1,
                "buyer_message": "Clean viewfinder? Offering $860.",
                "expiration_time": (now + timedelta(hours=31)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            {
                "best_offer_id": "BO-99388",
                "ebay_item_id": "220914800204",
                "sku": "VLT-REDWING-204",
                "buyer_username": "denim_archive",
                "price_cents": 23500,
                "status": BestOfferLifecycleStatus.SELLER_ACCEPT.value,
                "quantity": 1,
                "buyer_message": "Thanks! Completing checkout tonight.",
                "expiration_time": (now + timedelta(hours=14)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        ],
        "paid_orders": [
            {
                "order_id": "24-88510-10042",
                "ebay_item_id": "220914800207",
                "sku": "VLT-ARC-207",
                "title": "Arc'teryx Beta AR Gore-Tex Pro Shell Jacket Mens Large Cobalt",
                "quantity": 1,
                "total_cents": 38500,
                "buyer_username": "alpine_ridge_co",
                "paid_at": (now - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            {
                "order_id": "24-88510-10019",
                "ebay_item_id": "220914800206",
                "sku": "VLT-LEVIS-206",
                "title": "Vintage 1994 Levi's 501XX Made in USA Selvedge Jeans 32x30",
                "quantity": 1,
                "total_cents": 16800,
                "buyer_username": "selvedge_standard",
                "paid_at": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            {
                "order_id": "24-88510-10064",
                "ebay_item_id": "220914800208",
                "sku": "VLT-OLYMPUS-208",
                "title": "Olympus Stylus Epic mju-II 35mm f/2.8 Point & Shoot Film Camera",
                "quantity": 1,
                "total_cents": 34000,
                "buyer_username": "silver_halide_nyc",
                "paid_at": (now - timedelta(hours=1, minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            {
                "order_id": "24-88510-10081",
                "ebay_item_id": "220914800209",
                "sku": "VLT-PEND-209",
                "title": "Vintage 1970s Pendleton Board Shirt Virgin Wool Plaid Mens Large",
                "quantity": 1,
                "total_cents": 12400,
                "buyer_username": "coastline_goods",
                "paid_at": (now - timedelta(minutes=38)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        ],
        "comp_bands": {
            "VLT-LEICA-200": {"p25_cents": 239000, "median_cents": 258000, "p75_cents": 279000, "sample_count": 11},
            "VLT-J97-201": {"p25_cents": 26500, "median_cents": 31000, "p75_cents": 35500, "sample_count": 27},
            "VLT-FILSON-202": {"p25_cents": 23500, "median_cents": 27500, "p75_cents": 31000, "sample_count": 16},
            "VLT-CONTAX-203": {"p25_cents": 89000, "median_cents": 99500, "p75_cents": 112000, "sample_count": 13},
            "VLT-REDWING-204": {"p25_cents": 21500, "median_cents": 24900, "p75_cents": 28500, "sample_count": 21},
            "VLT-SONY-205": {"p25_cents": 19500, "median_cents": 22900, "p75_cents": 25500, "sample_count": 30},
            "VLT-LEVIS-206": {"p25_cents": 14500, "median_cents": 16800, "p75_cents": 19500, "sample_count": 24},
            "VLT-ARC-207": {"p25_cents": 34000, "median_cents": 38500, "p75_cents": 42500, "sample_count": 19},
            "VLT-OLYMPUS-208": {"p25_cents": 29500, "median_cents": 34000, "p75_cents": 37900, "sample_count": 17},
            "VLT-PEND-209": {"p25_cents": 9800, "median_cents": 12400, "p75_cents": 14900, "sample_count": 29},
        },
    }


def start_tour(tour_id: str, plugin_name: str | None = None) -> dict[str, Any]:
    """Reset `tour.db`, seed the chosen tour dataset, and mark the tour active."""
    if tour_id not in TOURS:
        raise ValueError(f"Unknown tour {tour_id!r}; choose from {sorted(TOURS)}")

    resolved_name = plugin_name or get_settings().plugin_name or PLUGIN_NAME
    data_dir = plugin_data_dir(resolved_name)
    for suffix in ("", "-wal", "-shm"):
        (data_dir / f"tour.db{suffix}").unlink(missing_ok=True)
        (data_dir / f"tour_cache.db{suffix}").unlink(missing_ok=True)
    (data_dir / f"packed_{tour_id}.json").unlink(missing_ok=True)

    meta = dict(TOURS[tour_id])
    set_active_tour_info(meta, resolved_name)

    from .adapter.mock import MockEbayAdapter

    mock_warm = MockEbayAdapter(persona=meta["persona"])
    for warm_cat in ("57988", "11483", "15709"):
        specs = mock_warm.get_category_aspects(warm_cat)
        put_cached_category_aspects(warm_cat, "EBAY_US", specs, plugin_name=resolved_name)

    conn = get_db(resolved_name, filename="tour.db")
    try:
        if tour_id == "closet":
            _seed_closet_tour(conn)
            upsert_haul_batch(
                haul_id="haul_closet_wknd",
                label="Weekend Closet & Tech Clearout",
                total_cost_cents=0,
                item_count=7,
                unlisted_pile_count=3,
                daily_sprint_target=3,
                notes="3 closet items left on chair — 9-min Snap & Speak sprint unlocks ~$115 eBay Balance.",
                plugin_name=resolved_name,
                filename="tour_cache.db",
            )
        else:
            _seed_vault_tour(conn)
            upsert_haul_batch(
                haul_id="haul_pdx_estate",
                label="Pacific Estate Camera & Workwear Haul",
                total_cost_cents=98000,
                item_count=14,
                unlisted_pile_count=4,
                daily_sprint_target=3,
                notes="10/14 cataloged · 4 jackets/lenses left in intake tote B.",
                plugin_name=resolved_name,
                filename="tour_cache.db",
            )
    finally:
        conn.close()
    return meta


def leave_tour(plugin_name: str | None = None) -> None:
    """Exit sample-data tour mode and return cleanly to the seller's real `data.db`."""
    resolved_name = plugin_name or get_settings().plugin_name or PLUGIN_NAME
    set_active_tour_info(None, resolved_name)


def _seed_closet_tour(conn: Any) -> None:
    now = datetime.now(timezone.utc)
    get_or_create_seller_profile(conn, now=now - timedelta(days=3))
    update_seller_capabilities(
        conn,
        AccountCapabilities(marketplace_id="EBAY_US", policies_opted_in=False),
        now=now - timedelta(days=3),
    )

    # 1. Staged draft awaiting approval (Casual C2C)
    sku_pat = "HRM-20261112-8F21"
    pat_aspects = {
        "Brand": "Patagonia",
        "Size": "L",
        "Size Type": "Regular",
        "Type": "Sweater",
        "Style": "Pullover",
        "Department": "Men",
        "Color": "Stonewash Grey",
        "Chest Size": '22.5"',
        "Garment Length": '27.5"',
    }
    upsert_item(
        conn,
        sku=sku_pat,
        source_mode=SourceMode.AGENT_CAPTURE,
        status=ItemStatus.STAGED,
        title="Patagonia Better Sweater 1/4 Zip Fleece Pullover Mens Large Stonewash",
        category_id="57988",
        condition_enum=ConditionEnum.USED_GOOD,
        list_price_cents=6500,
        shipping_est_cents=845,
        package_weight_oz=15.2,
        aspects=pat_aspects,
        flaws_disclosure="Light fleece pilling along cuffs; quarter-zip tracks smoothly.",
        media_urls=["https://i.ebayimg.com/images/g/mock-patagonia-front/s-l1600.jpg"],
        now=now - timedelta(minutes=6),
    )
    pat_payload = StagedListingPayload(
        sku=sku_pat,
        title="Patagonia Better Sweater 1/4 Zip Fleece Pullover Mens Large Stonewash",
        description=(
            "Pre-owned Patagonia Better Sweater quarter-zip fleece pullover in Stonewash Grey, Mens Large.\n\n"
            "Measurements (laid flat): Chest 22.5\" pit-to-pit · Length 27.5\" collar seam to hem.\n"
            "Condition disclosure: Light fleece pilling along cuffs; quarter-zip tracks smoothly."
        ),
        category_id="57988",
        condition=ConditionEnum.USED_GOOD,
        list_price_cents=6500,
        shipping_est_cents=845,
        package_weight_oz=15.2,
        aspects=pat_aspects,
        flaws_disclosure="Light fleece pilling along cuffs; quarter-zip tracks smoothly.",
        media_urls=["https://i.ebayimg.com/images/g/mock-patagonia-front/s-l1600.jpg"],
        route_mode="TRADING_XML_FALLBACK",
    )
    stage_pending_action(
        conn,
        action_type=ActionType.PUBLISH_LISTING,
        sku=sku_pat,
        payload=pat_payload.model_dump(mode="json"),
        preview_card_md=(
            "### Staged Listing Preview — Patagonia Better Sweater Large\n"
            "- **Price**: $65.00 (+ $8.45 Ground Advantage · 15.2 oz under-1-lb tier)\n"
            "- **Est. Net Payout**: $55.76 after 13.6% + $0.40 fee\n"
            "- **Route**: Casual C2C Fallback (No Business Policies required)"
        ),
        ttl_seconds=840,
        now=now,
    )
    append_item_event(
        conn,
        sku=sku_pat,
        event_type="LISTING_STAGED",
        actor=EventActor.AGENT_STAGED,
        detail={"list_price_cents": 6500, "route_mode": "TRADING_XML_C2C"},
        now=now - timedelta(minutes=6),
    )

    # 2. Active mobile Magical Listing needing QA cleanup (missing measurements + AI fluff)
    sku_levis = "HRM-20261111-2D19"
    upsert_item(
        conn,
        sku=sku_levis,
        ebay_item_id="110588300102",
        source_mode=SourceMode.MAGICAL_AUDIT,
        status=ItemStatus.ACTIVE,
        title="Levi's 501 Original Fit Straight Leg Blue Denim Jeans Mens 32x30",
        category_id="11483",
        condition_enum=ConditionEnum.USED_GOOD,
        list_price_cents=4600,
        shipping_est_cents=895,
        package_weight_oz=21.0,
        aspects={"Brand": "Levi's", "Size": "32", "Department": "Men", "Type": "Jeans"},
        flaws_disclosure="Faint wallet fade on back right pocket; hems intact.",
        media_urls=["https://i.ebayimg.com/images/g/mock-levis-501/s-l1600.jpg"],
        listed_at=(now - timedelta(days=1, hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        now=now - timedelta(minutes=12),
    )
    rev_payload = ReviseListingPayload(
        sku=sku_levis,
        ebay_item_id="110588300102",
        changes={
            "description": (
                "Pre-owned Levi's 501 Original Fit button-fly jeans. Measured flat: Waist 16\" (32\"actual) · "
                "Inseam 29.75\" · Leg opening 8\". Faint wallet fade on back right pocket; hems intact."
            ),
            "aspects": {
                "Brand": "Levi's",
                "Size": "32",
                "Size Type": "Regular",
                "Inseam": "30 in",
                "Waist Size": "32 in",
                "Department": "Men",
                "Type": "Jeans",
                "Style": "Straight",
            },
            "qa_warnings_resolved": [
                "Stripped mobile AI boilerplate ('Elevate your wardrobe', 'timeless must-have')",
                "Populated missing Size Type, Waist Size, Inseam, and Style aspects",
            ],
        },
    )
    stage_pending_action(
        conn,
        action_type=ActionType.REVISE_LISTING,
        sku=sku_levis,
        payload=rev_payload.model_dump(mode="json"),
        preview_card_md=(
            "### Magical Listing QA Revision — Levi's 501 Jeans (QA Score: 62 -> 96)\n"
            "- **Removed AI Fluff**: Stripped `'Elevate your wardrobe'` & `'timeless must-have'`\n"
            "- **Added Ruler Specs**: Waist `32 in`, Inseam `30 in`, Leg Opening `8\"`"
        ),
        ttl_seconds=780,
        now=now,
    )
    append_item_event(
        conn,
        sku=sku_levis,
        event_type="LISTING_AUDITED",
        actor=EventActor.AGENT_STAGED,
        detail={"qa_score": 62, "ebay_item_id": "110588300102"},
        now=now - timedelta(minutes=12),
    )

    # 3. Sold AirPods awaiting shipment
    upsert_item(
        conn,
        sku="HRM-20261110-4A9C",
        ebay_item_id="110588300103",
        source_mode=SourceMode.AGENT_CAPTURE,
        status=ItemStatus.SOLD,
        title="Apple AirPods Pro 2nd Gen MagSafe Case (USB-C) Complete",
        category_id="112529",
        condition_enum=ConditionEnum.USED_EXCELLENT,
        list_price_cents=15900,
        shipping_est_cents=695,
        package_weight_oz=7.4,
        aspects={"Brand": ["Apple"], "Model": ["AirPods Pro (2nd generation)"], "Connectivity": ["Bluetooth", "USB-C"]},
        flaws_disclosure="Tiny hairline pocket marks on charging case; ear tips unused.",
        listed_at=(now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        now=now - timedelta(hours=3),
    )
    append_item_event(
        conn,
        sku="HRM-20261110-4A9C",
        event_type="ORDER_PAID",
        actor=EventActor.EBAY_SYNC,
        detail={"order_id": "19-11842-77410", "total_cents": 15900},
        now=now - timedelta(hours=3),
    )

    # 4. Clean active Nintendo Switch OLED
    upsert_item(
        conn,
        sku="HRM-20261109-9E04",
        ebay_item_id="110588300104",
        source_mode=SourceMode.AGENT_CAPTURE,
        status=ItemStatus.ACTIVE,
        title="Nintendo Switch OLED Model 64GB White Console + Dock & Neon Joy-Cons",
        category_id="139971",
        condition_enum=ConditionEnum.USED_EXCELLENT,
        list_price_cents=24500,
        shipping_est_cents=1250,
        package_weight_oz=42.0,
        aspects={"Brand": ["Nintendo"], "Model": ["Nintendo Switch - OLED Model"], "Storage Capacity": ["64 GB"]},
        flaws_disclosure="Screen protector installed since day one; zero Joy-Con drift.",
        listed_at=(now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        now=now - timedelta(days=3),
    )
    append_item_event(
        conn,
        sku="HRM-20261109-9E04",
        event_type="LISTING_PUBLISHED",
        actor=EventActor.USER,
        detail={"ebay_item_id": "110588300104", "list_price_cents": 24500},
        now=now - timedelta(days=3),
    )


def _seed_vault_tour(conn: Any) -> None:
    now = datetime.now(timezone.utc)
    get_or_create_seller_profile(conn, now=now - timedelta(days=14))
    update_seller_capabilities(
        conn,
        AccountCapabilities(
            marketplace_id="EBAY_US",
            policies_opted_in=True,
            fulfillment_policy_id="FUL-VAULT-GROUND-01",
            payment_policy_id="PAY-IMMEDIATE-01",
            return_policy_id="RET-30DAY-PRO",
            merchant_location_key="PDX-WAREHOUSE-A",
        ),
        now=now - timedelta(days=14),
    )
    conn.execute("UPDATE seller_profile SET pro_ops_enabled = 1 WHERE profile_key = 'default'")
    conn.commit()

    # 1. Staged high-margin Leica camera awaiting approval
    leica_aspects = {
        "Brand": "Leica",
        "Model": "Leica M6 TTL 0.72",
        "Type": "Rangefinder",
        "Format": "35 mm",
        "Focus Type": "Manual",
        "Color": "Black Chrome",
        "Country/Region of Manufacture": "Germany",
    }
    upsert_item(
        conn,
        sku="VLT-LEICA-200",
        source_mode=SourceMode.AGENT_CAPTURE,
        status=ItemStatus.STAGED,
        title="Leica M6 TTL 0.72x Black Chrome 35mm Rangefinder Body CLA'd 2025",
        category_id="15230",
        condition_enum=ConditionEnum.USED_EXCELLENT,
        list_price_cents=259000,
        shipping_est_cents=2450,
        package_weight_oz=28.0,
        aspects=leica_aspects,
        flaws_disclosure="Faint brassing near strap lugs; meter accurate within 1/3 stop, curtains crisp.",
        cogs_cents=168000,
        bin_code="C-01-01",
        floor_price_cents=235000,
        now=now - timedelta(minutes=4),
    )
    leica_payload = StagedListingPayload(
        sku="VLT-LEICA-200",
        title="Leica M6 TTL 0.72x Black Chrome 35mm Rangefinder Body CLA'd 2025",
        description=(
            "Leica M6 TTL 0.72x Black Chrome 35mm rangefinder body (Serial #247xxxx).\n\n"
            "Shutter speeds & TTL light meter bench-tested accurate within 1/3 stop. Rangefinder patch bright "
            "and aligned horizontally/vertically. Faint strap-lug rub disclosed in close-up photos."
        ),
        category_id="15230",
        condition=ConditionEnum.USED_EXCELLENT,
        list_price_cents=259000,
        shipping_est_cents=2450,
        package_weight_oz=28.0,
        aspects=leica_aspects,
        flaws_disclosure="Faint brassing near strap lugs; meter accurate within 1/3 stop.",
        media_urls=["https://i.ebayimg.com/images/g/mock-leica-m6/s-l1600.jpg"],
        cogs_cents=168000,
        bin_code="C-01-01",
        floor_price_cents=235000,
        route_mode="INVENTORY_REST",
        merchant_location_key="PDX-WAREHOUSE-A",
        fulfillment_policy_id="FUL-VAULT-GROUND-01",
        payment_policy_id="PAY-IMMEDIATE-01",
        return_policy_id="RET-30DAY-PRO",
    )
    stage_pending_action(
        conn,
        action_type=ActionType.PUBLISH_LISTING,
        sku="VLT-LEICA-200",
        payload=leica_payload.model_dump(mode="json"),
        preview_card_md=(
            "### Staged Pro Inventory Publish — Leica M6 TTL 0.72x (`VLT-LEICA-200`)\n"
            "- **List**: $2,590.00 · **COGS**: $1,680.00 · **Est. Net Profit**: **$554.02 (21.4% margin)**\n"
            "- **Bin**: `C-01-01` · **Floor**: $2,350.00 · **Route**: Sell Inventory REST API"
        ),
        ttl_seconds=860,
        now=now,
    )
    append_item_event(
        conn,
        sku="VLT-LEICA-200",
        event_type="LISTING_STAGED",
        actor=EventActor.AGENT_STAGED,
        detail={"list_price_cents": 259000, "cogs_cents": 168000, "bin_code": "C-01-01"},
        now=now - timedelta(minutes=4),
    )

    # 2. Active Carhartt J97 Magical Listing needing QA cleanup (staged REVISE_LISTING)
    upsert_item(
        conn,
        sku="VLT-J97-201",
        ebay_item_id="220914800201",
        offer_id="OFF-J97-201",
        source_mode=SourceMode.MAGICAL_AUDIT,
        status=ItemStatus.ACTIVE,
        title="Vintage Carhartt J97 MOS Moss Green Blanket Lined Detroit Jacket Mens Large",
        category_id="57988",
        condition_enum=ConditionEnum.USED_GOOD,
        list_price_cents=31500,
        shipping_est_cents=1350,
        package_weight_oz=46.0,
        aspects={"Brand": ["Carhartt"], "Size": ["L"], "Color": ["Moss Green"], "Type": ["Jacket"]},
        flaws_disclosure="Honeycomb sleeve fade + tiny 3mm cuff fray on left wrist; blanket lining clean.",
        cogs_cents=8500,
        bin_code="A-03-02",
        floor_price_cents=27000,
        listed_at=(now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        now=now - timedelta(minutes=11),
    )
    j97_rev = ReviseListingPayload(
        sku="VLT-J97-201",
        ebay_item_id="220914800201",
        offer_id="OFF-J97-201",
        changes={
            "description": (
                "Authentic vintage Carhartt J97 MOS Sandstone Duck Detroit Jacket in Moss Green, Mens Large.\n\n"
                "Measurements (flat): Chest 25\" pit-to-pit · Length 25.5\" back collar base to hem · Sleeve 24.5\".\n"
                "Condition: Natural honeycomb sleeve fade and 3mm cuff fray on left wrist; YKK brass main & chest zippers smooth."
            ),
            "aspects": {
                "Brand": "Carhartt",
                "Size": "L",
                "Size Type": "Regular",
                "Department": "Men",
                "Type": "Jacket",
                "Style": "Work Jacket",
                "Color": "Moss Green",
                "Outer Shell Material": "Cotton Duck",
                "Chest Size": '25"',
                "Garment Length": '25.5"',
            },
            "qa_warnings_resolved": [
                "Removed mobile AI clichés ('Stunning rare gem', 'turn heads everywhere')",
                "Injected flat pit-to-pit (25\"), length (25.5\"), and sleeve (24.5\") measurements",
            ],
        },
    )
    stage_pending_action(
        conn,
        action_type=ActionType.REVISE_LISTING,
        sku="VLT-J97-201",
        payload=j97_rev.model_dump(mode="json"),
        preview_card_md=(
            "### Magical Listing QA Fix — Carhartt J97 Detroit Jacket (`VLT-J97-201`)\n"
            "- **QA Score**: `58 -> 97` · Stripped `'stunning rare gem'` fluff\n"
            "- **Measurements Added**: `25\"` pit-to-pit · `25.5\"` length · `24.5\"` sleeve"
        ),
        ttl_seconds=790,
        now=now,
    )
    append_item_event(
        conn,
        sku="VLT-J97-201",
        event_type="LISTING_AUDITED",
        actor=EventActor.AGENT_STAGED,
        detail={"qa_score": 58, "ebay_item_id": "220914800201"},
        now=now - timedelta(minutes=11),
    )

    # 3. Active Filson Cruiser with Active Best Offer ($245 vs $235 floor -> staged ACCEPT!)
    upsert_item(
        conn,
        sku="VLT-FILSON-202",
        ebay_item_id="220914800202",
        offer_id="OFF-FILSON-202",
        source_mode=SourceMode.AGENT_CAPTURE,
        status=ItemStatus.ACTIVE,
        title="Filson Style 110 Mackinaw Wool Cruiser Jacket Charcoal Mens 42 (Large)",
        category_id="57988",
        condition_enum=ConditionEnum.USED_EXCELLENT,
        list_price_cents=27500,
        shipping_est_cents=1450,
        package_weight_oz=52.0,
        package_dims={"length_in": 18.0, "width_in": 14.0, "height_in": 8.0},
        aspects={
            "Brand": ["Filson"],
            "Size": ["42"],
            "Size Type": ["Regular"],
            "Department": ["Men"],
            "Type": ["Coat"],
            "Style": ["Mackinaw Cruiser"],
            "Chest Size": ['23.5"'],
        },
        flaws_disclosure="No moth bites, odors, or cuff wear; all snaps tight.",
        cogs_cents=9200,
        bin_code="A-01-04",
        floor_price_cents=23500,
        listed_at=(now - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        now=now - timedelta(minutes=8),
    )
    filson_offer_payload = RespondOfferPayload(
        sku="VLT-FILSON-202",
        ebay_item_id="220914800202",
        best_offer_id="BO-99401",
        action=OfferDecisionAction.ACCEPT,
        seller_message="Thanks! Will pull from Bin A-01-04 and ship same day once checkout completes.",
    )
    stage_pending_action(
        conn,
        action_type=ActionType.RESPOND_OFFER,
        sku="VLT-FILSON-202",
        payload=filson_offer_payload.model_dump(mode="json"),
        preview_card_md=(
            "### Best Offer Decision — ACCEPT $245.00 on Filson Mackinaw Cruiser\n"
            "- **Offer**: $245.00 (>= $235.00 floor) · **Net Profit**: **$119.28 (48.7% margin)**\n"
            "- **2026 Lifecycle Note**: Accepting sets status to `SellerAccept`; item stays live until buyer pays."
        ),
        ttl_seconds=810,
        now=now,
    )

    # 4. Active Contax G2 Kit with below-floor offer ($860 vs $920 floor)
    upsert_item(
        conn,
        sku="VLT-CONTAX-203",
        ebay_item_id="220914800203",
        offer_id="OFF-CONTAX-203",
        source_mode=SourceMode.AGENT_CAPTURE,
        status=ItemStatus.ACTIVE,
        title="Contax G2 Titanium 35mm Autofocus Rangefinder + Carl Zeiss Planar 45mm f/2",
        category_id="15230",
        condition_enum=ConditionEnum.USED_EXCELLENT,
        list_price_cents=99500,
        shipping_est_cents=1850,
        package_weight_oz=34.0,
        aspects={"Brand": ["Contax"], "Model": ["Contax G2"], "Type": ["Rangefinder"], "Format": ["35 mm"]},
        flaws_disclosure="No LCD bleed on top frame counter; glass haze/fungus-free.",
        cogs_cents=59000,
        bin_code="C-02-01",
        floor_price_cents=92000,
        listed_at=(now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        now=now - timedelta(hours=6),
    )

    # 5. Active Red Wing 875 with SellerAccept offer (awaiting buyer payment!)
    upsert_item(
        conn,
        sku="VLT-REDWING-204",
        ebay_item_id="220914800204",
        offer_id="OFF-REDWING-204",
        source_mode=SourceMode.AGENT_CAPTURE,
        status=ItemStatus.ACTIVE,
        title="Red Wing Heritage 875 Classic Moc Toe Oro Legacy Leather Boots Mens 10D",
        category_id="15709",
        condition_enum=ConditionEnum.USED_GOOD,
        list_price_cents=25500,
        shipping_est_cents=1550,
        package_weight_oz=58.0,
        aspects={"Brand": ["Red Wing Shoes"], "US Shoe Size": ["10"], "Shoe Width": ["D"], "Style": ["Moc Toe"]},
        flaws_disclosure="Light outsole heel edge wear (~90% Traction Tred life left); conditioned with Bick 4.",
        cogs_cents=7800,
        bin_code="B-03-02",
        floor_price_cents=22500,
        listed_at=(now - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        now=now - timedelta(hours=4),
    )
    append_item_event(
        conn,
        sku="VLT-REDWING-204",
        event_type="OFFER_RESPONDED",
        actor=EventActor.USER,
        detail={"best_offer_id": "BO-99388", "decision_action": "Accept", "resulting_status": "SellerAccept"},
        now=now - timedelta(hours=4),
    )

    # 6. Active Sony Walkman (118 days active -> demonstrates 90-180d Cassini Stale Diagnostic!)
    upsert_item(
        conn,
        sku="VLT-SONY-205",
        ebay_item_id="220914800205",
        offer_id="OFF-SONY-205",
        source_mode=SourceMode.AGENT_CAPTURE,
        status=ItemStatus.ACTIVE,
        title="Sony Walkman WM-EX677 Metallic Silver Cassette Player New Belt Serviced",
        category_id="15053",
        condition_enum=ConditionEnum.USED_EXCELLENT,
        list_price_cents=22900,
        shipping_est_cents=895,
        package_weight_oz=11.5,
        package_dims={"length_in": 12.0, "width_in": 10.0, "height_in": 4.0},
        aspects={
            "Brand": ["Sony"],
            "Model": ["WM-EX677"],
            "Type": ["Portable Cassette Player"],
            "Playable Media Format": ["Cassette Tape"],
        },
        flaws_disclosure="New Marian drive belt installed; pinch roller cleaned, wow/flutter tested.",
        cogs_cents=6400,
        bin_code="C-03-04",
        floor_price_cents=19500,
        listed_at=(now - timedelta(days=118)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        now=now - timedelta(days=2),
    )

    # 7-10. Four PAID orders ready for sorted warehouse bin picking!
    sold_rows = [
        (
            "VLT-LEVIS-206",
            "220914800206",
            "Vintage 1994 Levi's 501XX Made in USA Selvedge Jeans 32x30",
            "11483",
            16800,
            3400,
            "A-01-02",
            22.0,
        ),
        (
            "VLT-ARC-207",
            "220914800207",
            "Arc'teryx Beta AR Gore-Tex Pro Shell Jacket Mens Large Cobalt",
            "57988",
            38500,
            13500,
            "A-02-04",
            19.5,
        ),
        (
            "VLT-PEND-209",
            "220914800209",
            "Vintage 1970s Pendleton Board Shirt Virgin Wool Plaid Mens Large",
            "57988",
            12400,
            2200,
            "B-01-03",
            17.0,
        ),
        (
            "VLT-OLYMPUS-208",
            "220914800208",
            "Olympus Stylus Epic mju-II 35mm f/2.8 Point & Shoot Film Camera",
            "15230",
            34000,
            11000,
            "C-02-02",
            9.2,
        ),
    ]
    for idx, (sku, item_id, title, cat_id, price, cogs, bin_code, weight_oz) in enumerate(sold_rows):
        upsert_item(
            conn,
            sku=sku,
            ebay_item_id=item_id,
            source_mode=SourceMode.AGENT_CAPTURE,
            status=ItemStatus.SOLD,
            title=title,
            category_id=cat_id,
            condition_enum=ConditionEnum.USED_EXCELLENT,
            list_price_cents=price,
            shipping_est_cents=950,
            package_weight_oz=weight_oz,
            aspects={"Brand": [title.split()[1] if title.startswith("Vintage") else title.split()[0]]},
            flaws_disclosure="Inspected, steamed/tested, poly-bagged in assigned warehouse bin.",
            cogs_cents=cogs,
            bin_code=bin_code,
            floor_price_cents=int(price * 0.85),
            listed_at=(now - timedelta(days=5 - idx)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            now=now - timedelta(hours=idx + 1),
        )
        append_item_event(
            conn,
            sku=sku,
            event_type="ORDER_PAID",
            actor=EventActor.EBAY_SYNC,
            detail={"bin_code": bin_code, "list_price_cents": price},
            now=now - timedelta(hours=idx + 1),
        )
