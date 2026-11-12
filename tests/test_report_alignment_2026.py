"""Comprehensive 2026 Report Alignment verification across Tiers 1, 2, and 3:
- Tier 1: July 2026 USPS 139-DIM engine, Jan 2026 PLG + ~7% buyer tax FVF + Very High INAD (+5%),
  $200-$500 Ship With eBay fashion guard, Aug 2026 Size Standardization, 2990/3000/3010 fashion IDs,
  Vintage Catalog-Number Defense, Motors >$10 free returns, May 2026 coin grading, Nov 2026 duplicate/VeRO
  pre-screens, strict Windows CRLF Seller Hub Draft CSV export, SQLite Taxonomy aspect cache, and Browse
  <=100-char query truncation + search_by_image.
- Tier 2: 90-180d Cassini Stale Diagnostics (IN_PLACE_ASPECT_UPGRADE vs END_AND_RECREATE_5PCT), Haul /
  Death-Pile break-even velocity, and orphaned inkFrog / myFitment CSV importer -> <=25 SKUs/call Bulk Publish.
- Tier 3: Zero-LLM-token (--no-agent) cron scripts ([SILENT] suppression vs actionable digest) and FastAPI
  routes (/export/draft-csv, /import/csv, /haul, /cron/{job_id}/run).
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_ebay_ops.adapter import LiveEbayAdapter, MockEbayAdapter, set_adapter_override
from hermes_ebay_ops.compliance import screen_listing_compliance, validate_apparel_size_standard
from hermes_ebay_ops.config import configure_settings
from hermes_ebay_ops.db import get_db, upsert_item
from hermes_ebay_ops.models import ConditionEnum, ItemStatus, SourceMode
from hermes_ebay_ops.scripts import run_cron_job
from hermes_ebay_ops.tools import compute_dimensional_shipping, compute_economics
from hermes_ebay_ops.tools.phase1_mvp import (
    handle_check_comps,
    handle_confirm_action,
    handle_export_draft_csv,
    handle_stage_listing,
)
from hermes_ebay_ops.tools.phase2_ops import handle_diagnose_stale, handle_import_migration_csv

REPO_ROOT = Path(__file__).resolve().parent.parent


def _build_api_client() -> TestClient:
    api_path = REPO_ROOT / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("test_report_alignment_api", api_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/plugins/hermes-ebay-ops")
    return TestClient(app)


def test_tier1_july_2026_usps_139_dim_oversize_and_fashion_ship_cap() -> None:
    # 18 x 14 x 8 = 2,016 cu in (> 1,728 cu in threshold)
    # July 12, 2026 divisor 139 -> ceil(2016 / 139) = 15 lbs (legacy 166 would have been 13 lbs)
    dim = compute_dimensional_shipping(
        48.0,
        {"length_in": 18, "width_in": 14, "height_in": 8},
        list_price_cents=34500,
        category_id="57988",  # Fashion outerwear in $200-$500 band
        fulfillment_policy_id="FUL-100",
    )
    assert dim.cubic_inches == 2016.0
    assert dim.dim_triggered is True
    assert dim.dim_lbs_2026 == 15
    assert dim.billable_lbs == 15
    assert dim.recommended_box_code == "BOX-PARKA"
    assert any("139" in w for w in dim.warnings)
    assert any("$200–$500" in w or "$200-$500" in w for w in dim.warnings)

    # Oversize >48 in side alert
    oversize = compute_dimensional_shipping(
        64.0,
        {"length_in": 50, "width_in": 8, "height_in": 6},
    )
    assert oversize.oversize_triggered is True
    assert any("48" in w for w in oversize.warnings)


def test_tier1_jan_2026_plg_sales_tax_fvf_and_inad_risk_economics() -> None:
    base = compute_economics(
        list_price_cents=10000,
        shipping_est_cents=1000,
        estimated_fee_bps=1360,
        cogs_cents=3000,
    )
    plg_tax = compute_economics(
        list_price_cents=10000,
        shipping_est_cents=1000,
        estimated_fee_bps=1360,
        cogs_cents=3000,
        plg_ad_rate_bps=500,
        buyer_tax_rate_bps=700,
    )
    # Gross = $110.00; +7% buyer tax basis = $117.70 -> PLG 5% = round(588.5) = 588 cents
    assert plg_tax.plg_ad_fee_cents == 588
    assert plg_tax.very_high_inad_risk_cents == 588
    assert plg_tax.estimated_fees_cents > base.estimated_fees_cents
    assert plg_tax.estimated_net_payout_cents < base.estimated_net_payout_cents


def test_tier1_category_compliance_guards_size_vintage_motors_coins_and_duplicate() -> None:
    # 1. Aug 2026 Apparel Size Standardization + 2990/3000/3010 fashion condition IDs
    ok_bad, msg_bad = validate_apparel_size_standard("Fits like Medium")
    assert ok_bad is False and msg_bad is not None
    ok_good, msg_good = validate_apparel_size_standard("L")
    assert ok_good is True and msg_good is None
    assert ConditionEnum.USED_EXCELLENT.browse_condition_id("57988") == "2990"
    assert ConditionEnum.USED_GOOD.browse_condition_id("57988") == "3000"
    assert ConditionEnum.USED_ACCEPTABLE.browse_condition_id("57988") == "3010"
    assert ConditionEnum.USED_EXCELLENT.browse_condition_id("15230") == "3000"

    # 2. Vintage Catalog-Number Defense: mold #443 moved out of MPN into Pattern
    vint = screen_listing_compliance(
        title="Vintage Pyrex Verde Mixing Bowl #443 2.5 Qt",
        description="Clean glossy finish, zero chips.",
        category_id="870",
        condition=ConditionEnum.USED_EXCELLENT,
        list_price_cents=4200,
        aspects={"Brand": ["Pyrex"], "MPN": ["#443"]},
        media_urls_or_paths=["https://i.ebayimg.com/images/g/pyr/s-l1600.jpg"],
    )
    assert vint.playbook == "ebay-vintage-collectibles"
    assert vint.sanitized_aspects["MPN"] == "Does Not Apply"
    assert vint.sanitized_aspects["Pattern"] == "#443"
    assert any("Catalog-Number Defense" in w for w in vint.warnings)

    # 3. Motors >$10 New part missing MPN & free returns warning
    mot = screen_listing_compliance(
        title="Downstream Oxygen Sensor Toyota Tacoma 4.0L",
        description="Brand new replacement sensor.",
        category_id="33615",
        condition=ConditionEnum.NEW,
        list_price_cents=6400,
        aspects={"Brand": ["Denso"]},
        media_urls_or_paths=["https://i.ebayimg.com/images/g/mot/s-l1600.jpg"],
        return_policy_id=None,
    )
    assert mot.playbook == "ebay-motors-fitment"
    assert any("MPN" in w for w in mot.warnings)
    assert any("30-day free return" in w.lower() for w in mot.warnings)

    # 4. May 2026 Coin Condition Grading + VeRO + Watermark primary image screen
    coin = screen_listing_compliance(
        title="1921 Morgan Silver Dollar Velcro Pouch Included",
        description="Bright luster ungraded dollar.",
        category_id="11116",
        condition=ConditionEnum.USED_EXCELLENT,
        list_price_cents=4800,
        aspects={"Year": ["1921"]},
        media_urls_or_paths=["watermark_logo_main.jpg"],
    )
    assert coin.playbook == "ebay-cards-coins-media"
    assert any("May 2026 Coin" in w for w in coin.warnings)
    assert any("VeRO" in w for w in coin.warnings)
    assert any("watermark" in w.lower() for w in coin.warnings)

    # 5. Nov 26, 2026 Duplicate Active Fixed-Price Title Guard
    conn = get_db()
    upsert_item(
        conn,
        sku="DUP-ACTIVE-1",
        ebay_item_id="v1|285900112233|0",
        title="Leica M6 TTL 0.72 Black Chrome 35mm Rangefinder Body",
        category_id="15230",
        condition_enum=ConditionEnum.USED_EXCELLENT,
        list_price_cents=289500,
        status=ItemStatus.ACTIVE,
        source_mode=SourceMode.AGENT_CAPTURE,
    )
    dup = screen_listing_compliance(
        title="Leica M6 TTL 0.72 Black Chrome 35mm Rangefinder Body",
        description="Clean rangefinder.",
        category_id="15230",
        condition=ConditionEnum.USED_EXCELLENT,
        list_price_cents=289500,
        aspects={"Brand": ["Leica"]},
        media_urls_or_paths=["camera.jpg"],
        conn=conn,
    )
    assert any("Duplicate Listings Policy" in w for w in dup.warnings)


def test_tier1_windows_crlf_seller_hub_draft_csv_sanitizes_newlines(tmp_path: Path) -> None:
    mock_adapter = MockEbayAdapter(persona="lite")
    set_adapter_override(mock_adapter)

    img = tmp_path / "jacket.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0PHOTO\xff\xd9")

    stage_res = json.loads(
        handle_stage_listing(
            {
                "media_paths": [str(img)],
                "title_hint": "Patagonia Down Sweater Mens Large Black",
                "seller_notes": "Line one measurement 23 in pit to pit.\nLine two cuff note\r\nClean zippers.",
                "price_cents": 12500,
            }
        )
    )
    assert stage_res["ok"] is True

    csv_res = json.loads(handle_export_draft_csv({"skus": [stage_res["sku"]]}))
    assert csv_res["ok"] is True
    assert csv_res["row_count"] == 1
    assert csv_res["crlf_verified"] is True

    raw_csv: str = csv_res["csv_text"]
    assert "\r\n" in raw_csv
    # Every CRLF split produces exact header row + 1 data row (no stray LF splits inside cells)
    lines = raw_csv.rstrip("\r\n").split("\r\n")
    assert len(lines) == 2
    for line in lines:
        assert "\n" not in line and "\r" not in line
    assert lines[0].startswith("*Action(")
    assert lines[-1].startswith("Draft,")


def test_tier1_taxonomy_sqlite_cache_browse_100char_truncation_and_image_comps(monkeypatch: Any) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_provider_request(
        provider: str,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        **kwargs: Any,
    ) -> Any:
        from hermes_cli.plugin_provider_requests import ProviderResponse

        calls.append((method, url, params))
        if "get_default_category_tree_id" in url:
            return ProviderResponse(200, {}, json.dumps({"categoryTreeId": "0"}).encode())
        if "get_item_condition_policies" in url:
            return ProviderResponse(200, {}, json.dumps({"itemConditionPolicies": []}).encode())
        if "get_item_aspects_for_category" in url:
            body = {
                "aspects": [
                    {
                        "localizedAspectName": "Brand",
                        "aspectConstraint": {"aspectRequired": True, "aspectUsage": "REQUIRED"},
                    }
                ]
            }
            return ProviderResponse(200, {}, json.dumps(body).encode())
        if "search_by_image" in url:
            body = {"itemSummaries": [{"price": {"value": "88.00"}, "shippingOptions": [{"shippingCost": {"value": "0.00"}}]}]}
            return ProviderResponse(200, {}, json.dumps(body).encode())
        if "item_summary/search" in url:
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(url).query)
            assert len(qs["q"][0]) <= 100
            # Fashion category 57988 + USED_EXCELLENT must map to conditionIds:{2990}
            assert "2990" in qs["filter"][0]
            body = {"itemSummaries": [{"price": {"value": "95.00"}, "shippingOptions": [{"shippingCost": {"value": "5.00"}}]}]}
            return ProviderResponse(200, {}, json.dumps(body).encode())
        raise AssertionError(f"Unexpected URL {url}")

    import hermes_ebay_ops.adapter.live as live_mod

    monkeypatch.setattr(live_mod, "credentialed_provider_request", fake_provider_request)

    adapter = LiveEbayAdapter()
    specs_first = adapter.get_category_aspects("57988", "EBAY_US")
    specs_second = adapter.get_category_aspects("57988", "EBAY_US")
    assert len(specs_first) == 1 and len(specs_second) == 1
    # Second call must hit SQLite cache.db -> only 1 upstream get_item_aspects_for_category call!
    taxonomy_calls = [c for c in calls if "get_item_aspects_for_category" in c[1]]
    assert len(taxonomy_calls) == 1

    long_query = "Vintage MensHeavyweight Wool Mackinaw Cruiser Jacket Red Black Plaid Pocket Detail Extra Words Beyond Hundred Chars Limit Check"
    assert len(long_query) > 100
    comp_band = adapter.search_comps(long_query, "57988", ConditionEnum.USED_EXCELLENT.value)
    assert comp_band.sample_count == 1

    set_adapter_override(adapter)
    img_comp_res = json.loads(
        handle_check_comps(
            {
                "query": "Vintage Pyrex Bowl",
                "category_id": "870",
                "image_b64": "dGVzdC1pbWFnZS1ieXRlcw==",
            }
        )
    )
    assert img_comp_res["ok"] is True
    assert img_comp_res["search_mode"] == "image"


def test_tier2_inkfrog_and_myfitment_csv_migration_to_bulk_publish_25_skus() -> None:
    mock_adapter = MockEbayAdapter(persona="pro")
    set_adapter_override(mock_adapter)

    # 1. inkFrog CSV with legacy HTML wrapper + style block
    inkfrog_csv = (
        'CustomLabel,Title,CategoryID,StartPrice,ConditionID,Description,PicURL\n'
        'IF-SKU-1,"Patagonia Baggies Shorts Mens Large",57988,45.00,3000,'
        '"<div class=\\"inkfrog-wrapper\\"><style>.x{color:red}</style><p>Clean shell, 5 in inseam.</p></div>",'
        'https://i.ebayimg.com/images/g/1/s-l1600.jpg\n'
    )
    imp_if = json.loads(handle_import_migration_csv({"csv_text": inkfrog_csv}))
    assert imp_if["ok"] is True
    assert imp_if["detected_format"] == "inkfrog"
    assert imp_if["imported_count"] == 1
    assert mock_adapter.write_calls == []

    # 2. myFitment multi-row YMM vehicle compatibility CSV -> aggregates into 1 SKU + executes bulk_publish_listings
    myfitment_csv = (
        "PartNumber,Title,CategoryID,Price,ConditionID,Year,Make,Model,Trim,Notes\n"
        "MF-O2-900,Denso 234-4260 Oxygen Sensor Toyota Tacoma,33615,64.00,1000,2012,Toyota,Tacoma,Base,OEM sensor\n"
        "MF-O2-900,Denso 234-4260 Oxygen Sensor Toyota Tacoma,33615,64.00,1000,2013,Toyota,Tacoma,TRD,OEM sensor\n"
    )
    imp_mf = json.loads(handle_import_migration_csv({"csv_text": myfitment_csv}))
    assert imp_mf["ok"] is True
    assert imp_mf["detected_format"] == "myfitment"
    assert imp_mf["imported_count"] == 1

    # Approve the BULK_PUBLISH token via two-step gate
    confirm_res = json.loads(
        handle_confirm_action({"action_token": imp_mf["action_token"], "decision": "APPROVE"})
    )
    assert confirm_res["ok"] is True
    assert confirm_res["action_type"] == "BULK_PUBLISH"
    assert confirm_res["published_count"] == 1
    assert mock_adapter.write_calls[-1][0] == "bulk_publish_listings"


def test_tier2_and_tier3_cassini_stale_haul_cron_and_fastapi_endpoints(isolated_plugin_storage: Path) -> None:
    configure_settings({"adapter_mode": "mock", "mock_persona": "pro", "mode": "pro"})
    mock_adapter = MockEbayAdapter(persona="pro")
    set_adapter_override(mock_adapter)

    now = datetime.now(timezone.utc)
    conn = get_db()
    try:
        # 95-day-old sparse item -> IN_PLACE_ASPECT_UPGRADE
        upsert_item(
            conn,
            sku="STALE-SPARSE-01",
            ebay_item_id="110900001",
            source_mode=SourceMode.AGENT_CAPTURE,
            status=ItemStatus.ACTIVE,
            title="Patagonia Fleece Jacket Mens Large",
            category_id="57988",
            condition_enum=ConditionEnum.USED_GOOD,
            list_price_cents=7000,
            aspects={"Brand": ["Patagonia"]},
            listed_at=(now - timedelta(days=98)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        # 190-day-old well-specified item -> END_AND_RECREATE_5PCT
        upsert_item(
            conn,
            sku="STALE-AGED-02",
            ebay_item_id="110900002",
            source_mode=SourceMode.AGENT_CAPTURE,
            status=ItemStatus.ACTIVE,
            title="Leica M3 Double Stroke 35mm Rangefinder Body",
            category_id="15230",
            condition_enum=ConditionEnum.USED_EXCELLENT,
            list_price_cents=180000,
            aspects={"Brand": ["Leica"], "Type": ["Rangefinder"], "Model": ["M3"], "Format": ["35 mm"], "Focus Type": ["Manual"]},
            listed_at=(now - timedelta(days=195)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    finally:
        conn.close()

    diag_res = json.loads(handle_diagnose_stale({"min_age_days": 90}))
    assert diag_res["ok"] is True
    by_sku = {r["sku"]: r for r in diag_res["stale_listings"]}
    assert by_sku["STALE-SPARSE-01"]["strategy"] == "IN_PLACE_ASPECT_UPGRADE"
    assert by_sku["STALE-AGED-02"]["strategy"] == "END_AND_RECREATE_5PCT"
    assert by_sku["STALE-AGED-02"]["suggested_price_cents"] < 180000

    # Zero-token cron job returns $0 token digest when stale items exist, and [SILENT] when haul queue is empty
    stale_cron = run_cron_job("ebay-stale-cassini-check")
    assert stale_cron["llm_tokens_used"] == 0
    assert stale_cron["silent"] is False
    assert "STALE-AGED-02" in stale_cron["output"]

    empty_haul_cron = run_cron_job("ebay-evening-haul-sprint")
    assert empty_haul_cron["silent"] is True
    assert empty_haul_cron["output"] == "[SILENT]"

    # Verify new FastAPI endpoints (/haul, /cron/{id}/run, /export/draft-csv, /import/csv)
    client = _build_api_client()
    base = "/api/plugins/hermes-ebay-ops"

    r_haul = client.post(
        f"{base}/haul",
        json={
            "label": "Estate Camera Lot",
            "total_cost_cents": 45000,
            "item_count": 10,
            "unlisted_pile_count": 4,
            "daily_sprint_target": 3,
        },
    )
    assert r_haul.status_code == 200
    assert r_haul.json()["ok"] is True

    r_cron = client.post(f"{base}/cron/ebay-evening-haul-sprint/run", json={})
    assert r_cron.status_code == 200
    assert r_cron.json()["silent"] is False
    assert "Estate Camera Lot" in r_cron.json()["output"]

    r_csv = client.post(f"{base}/export/draft-csv", json={"status_filter": "ALL"})
    assert r_csv.status_code == 200
    assert r_csv.json()["crlf_verified"] is True

    r_imp = client.post(
        f"{base}/import/csv",
        json={
            "csv_text": "CustomLabel,Title,CategoryID,StartPrice,ConditionID\nIMP-01,Olympus Stylus Epic 35mm Camera,15230,295.00,3000\n"
        },
    )
    assert r_imp.status_code == 200
    assert r_imp.json()["imported_count"] == 1
