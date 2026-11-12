"""FastAPI backend for the Vendere (hermes-ebay-ops) Dashboard tab and Seller Studio Webapp.

Mounted automatically by Hermes at `/api/plugins/hermes-ebay-ops/`.
Every handler is a synchronous `def` so FastAPI dispatches it in a worker thread off the event loop.
All live write executions still pass strictly through `handle_confirm_action` and its cryptographic
SHA-256 + 15-minute TTL two-step approval gate.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

router = APIRouter()
PLUGIN_DIR = Path(__file__).resolve().parent.parent
PACKAGE_ALIAS = "vendere_dashboard_pkg"
_DESKTOP_HEARTBEAT_AT: float = 0.0


def _pkg() -> Any:
    """Import the plugin package cleanly even when plugin_api.py is loaded standalone by the dashboard."""
    for mod in list(sys.modules.values()):
        try:
            if getattr(mod, "__file__", None) == str(PLUGIN_DIR / "__init__.py"):
                return mod
        except Exception:
            continue
    if PACKAGE_ALIAS not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PACKAGE_ALIAS,
            PLUGIN_DIR / "__init__.py",
            submodule_search_locations=[str(PLUGIN_DIR)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load hermes-ebay-ops package for dashboard API")
        module = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE_ALIAS] = module
        spec.loader.exec_module(module)
    return sys.modules[PACKAGE_ALIAS]


def _submod(rel: str) -> Any:
    pkg = _pkg()
    return importlib.import_module(f"{pkg.__name__}.{rel}")


def _natural_bin_key(bin_code: str | None) -> tuple[Any, ...]:
    if not bin_code or not bin_code.strip():
        return (1, "ZZZZZZ")
    parts = re.split(r"(\d+)", bin_code.strip().upper())
    typed: list[Any] = [0]
    for p in parts:
        typed.append(int(p) if p.isdigit() else p)
    return tuple(typed)


def _sync_plugin_settings_from_host() -> Any:
    config_mod = _submod("config")
    try:
        from hermes_cli.config import load_config_readonly

        cfg = load_config_readonly() or {}
        entry = ((cfg.get("plugins") or {}).get("entries") or {}).get(config_mod.PLUGIN_NAME) or {}
        raw_settings = entry.get("settings") or entry.get("config") or {}
        if isinstance(raw_settings, dict) and raw_settings:
            defaults = config_mod.PluginSettings().model_dump()
            defaults.update({k: v for k, v in raw_settings.items() if v is not None})
            return config_mod.configure_settings(defaults)
    except Exception:
        pass
    return config_mod.get_settings()


def _compute_item_qa(item: dict[str, Any], aspects: dict[str, list[str]], pending_by_sku: dict[str, list[dict[str, Any]]]) -> tuple[int, list[str]]:
    score = 96
    flags: list[str] = []
    cat_id = str(item.get("category_id") or "")
    has_pending_revise = any(p["action_type"] == "REVISE_LISTING" for p in pending_by_sku.get(item["sku"], []))

    if item.get("source_mode") == "MAGICAL_AUDIT" and has_pending_revise:
        score -= 28
        flags.append("Mobile AI draft: review staged QA cleanup")
    if cat_id in {"57988", "11483"} and not any(k in aspects for k in ("Chest Size", "Inseam", "Waist Size", "Garment Length")):
        score -= 18
        flags.append("Missing flat ruler measurements (INAD risk)")
    if not item.get("flaws_disclosure"):
        score -= 8
        flags.append("No explicit condition/flaw note recorded")
    if item.get("package_weight_oz") is None:
        score -= 6
        flags.append("Packed weight unverified")

    return max(35, min(100, score)), flags


@router.post("/hello")
def hello() -> dict[str, Any]:
    global _DESKTOP_HEARTBEAT_AT
    _DESKTOP_HEARTBEAT_AT = time.time()
    db_mod = _submod("db")
    tour = db_mod.get_active_tour_info()
    return {"ok": True, "tour": tour}


@router.get("/state")
def get_state() -> dict[str, Any]:
    settings = _sync_plugin_settings_from_host()
    db_mod = _submod("db")
    demo_mod = _submod("demo")
    tools_mod = _submod("tools")
    adapter_mod = _submod("adapter")
    compliance_mod = _submod("compliance")
    diagnostics_mod = _submod("diagnostics")
    cache_mod = _submod("cache_store")
    scripts_mod = _submod("scripts")
    models_mod = _submod("models")
    storage_mod = importlib.import_module("plugins.plugin_storage")

    tour = db_mod.get_active_tour_info(settings.plugin_name)
    now = datetime.now(timezone.utc)
    week_ago_iso = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = db_mod.get_db(settings.plugin_name)
    try:
        profile = db_mod.get_or_create_seller_profile(conn)
        pro_active = (
            True
            if settings.mode == "pro"
            else False
            if settings.mode == "lite"
            else bool(profile.get("pro_ops_enabled"))
        )
        fee_bps = int(profile.get("estimated_fee_bps") or settings.default_estimated_fee_bps)

        # Expire stale pending actions opportunistically on read so UI countdowns stay truthful
        raw_pending = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM pending_actions WHERE status = 'PENDING' ORDER BY created_at DESC"
            ).fetchall()
        ]
        active_pending: list[dict[str, Any]] = []
        pending_by_sku: dict[str, list[dict[str, Any]]] = {}
        for p in raw_pending:
            exp_dt = db_mod.parse_iso_utc(str(p["expires_at"]))
            rem = int((exp_dt - now).total_seconds())
            if rem <= 0:
                conn.execute(
                    "UPDATE pending_actions SET status = 'EXPIRED' WHERE action_token = ?",
                    (p["action_token"],),
                )
                continue
            payload_obj = json.loads(p["payload_json"]) if p.get("payload_json") else {}
            entry = {
                "action_token": p["action_token"],
                "action_type": p["action_type"],
                "sku": p["sku"],
                "payload": payload_obj,
                "payload_sha256": p["payload_sha256"][:12],
                "preview_card_md": p["preview_card_md"],
                "expires_at": p["expires_at"],
                "created_at": p["created_at"],
                "seconds_remaining": rem,
            }
            active_pending.append(entry)
            if p["sku"]:
                pending_by_sku.setdefault(str(p["sku"]), []).append(entry)
        conn.commit()

        raw_items = [
            dict(r)
            for r in conn.execute("SELECT * FROM items ORDER BY updated_at DESC").fetchall()
        ]

        tour_extras = demo_mod.get_tour_demo_extras(tour["id"]) if tour else None
        comp_bands = (tour_extras or {}).get("comp_bands") or {}

        items_out: list[dict[str, Any]] = []
        items_by_sku: dict[str, dict[str, Any]] = {}
        items_by_ebay_id: dict[str, dict[str, Any]] = {}

        active_value_cents = 0
        staged_value_cents = 0
        sold_value_cents = 0
        est_net_proceeds_cents = 0
        est_fees_total_cents = 0
        cogs_total_cents = 0
        potential_profit_cents = 0
        margins: list[float] = []
        qa_alerts_count = 0

        for row in raw_items:
            aspects = json.loads(row.get("aspects_json") or "{}")
            media_urls = json.loads(row.get("media_urls_json") or "[]")
            pkg_dims = json.loads(row["package_dims_json"]) if row.get("package_dims_json") else None
            econ = tools_mod.compute_economics(
                list_price_cents=int(row["list_price_cents"]),
                shipping_est_cents=int(row.get("shipping_est_cents") or 0),
                estimated_fee_bps=fee_bps,
                cogs_cents=row.get("cogs_cents"),
            )
            econ_plg5 = tools_mod.compute_economics(
                list_price_cents=int(row["list_price_cents"]),
                shipping_est_cents=int(row.get("shipping_est_cents") or 0),
                estimated_fee_bps=fee_bps,
                cogs_cents=row.get("cogs_cents"),
                plg_ad_rate_bps=500,
                buyer_tax_rate_bps=700,
            )
            dim_check = tools_mod.compute_dimensional_shipping(
                row.get("package_weight_oz"),
                pkg_dims,
                list_price_cents=int(row["list_price_cents"]),
                category_id=str(row["category_id"]),
                fulfillment_policy_id=profile.get("fulfillment_policy_id"),
            )
            cond_obj = models_mod.normalize_condition(row["condition_enum"])
            comp_rep = compliance_mod.screen_listing_compliance(
                title=str(row["title"]),
                description=str(row.get("flaws_disclosure") or row["title"]),
                category_id=str(row["category_id"]),
                condition=cond_obj,
                list_price_cents=int(row["list_price_cents"]),
                aspects=aspects,
                media_urls_or_paths=media_urls,
                return_policy_id=profile.get("return_policy_id"),
                sku=str(row["sku"]),
            )
            qa_score, qa_flags = _compute_item_qa(row, aspects, pending_by_sku)
            for extra_warn in [*comp_rep.warnings[:2], *dim_check.warnings[:2]]:
                if extra_warn not in qa_flags:
                    qa_flags.append(extra_warn)
            if qa_flags and row["status"] in {"STAGED", "ACTIVE"}:
                qa_alerts_count += 1

            status = str(row["status"])
            if status == "ACTIVE":
                active_value_cents += econ.list_price_cents
                est_net_proceeds_cents += econ.estimated_net_payout_cents
                est_fees_total_cents += econ.estimated_fees_cents
                if econ.cogs_cents is not None and econ.net_profit_cents is not None:
                    cogs_total_cents += econ.cogs_cents
                    potential_profit_cents += econ.net_profit_cents
                if econ.margin_pct is not None:
                    margins.append(econ.margin_pct)
            elif status == "STAGED":
                staged_value_cents += econ.list_price_cents
            elif status == "SOLD":
                sold_value_cents += econ.list_price_cents

            sku = str(row["sku"])
            enriched = {
                "sku": sku,
                "ebay_item_id": row.get("ebay_item_id"),
                "offer_id": row.get("offer_id"),
                "source_mode": row["source_mode"],
                "status": status,
                "title": row["title"],
                "category_id": row["category_id"],
                "playbook": comp_rep.playbook,
                "condition_enum": row["condition_enum"],
                "browse_condition_id": cond_obj.browse_condition_id(row["category_id"]),
                "list_price_cents": econ.list_price_cents,
                "shipping_est_cents": econ.shipping_est_cents,
                "package_weight_oz": row.get("package_weight_oz"),
                "package_dims": pkg_dims,
                "dim_check": {
                    "cubic_inches": dim_check.cubic_inches,
                    "actual_lbs": dim_check.actual_lbs,
                    "dim_lbs_2026": dim_check.dim_lbs_2026,
                    "billable_lbs": dim_check.billable_lbs,
                    "dim_triggered": dim_check.dim_triggered,
                    "oversize_triggered": dim_check.oversize_triggered,
                    "recommended_box_code": dim_check.recommended_box_code,
                    "summary_note": dim_check.summary_note,
                    "warnings": dim_check.warnings,
                },
                "weight_note": dim_check.summary_note,
                "aspects": aspects,
                "aspect_count": len(aspects),
                "flaws_disclosure": row.get("flaws_disclosure") or "",
                "media_urls": media_urls,
                "cogs_cents": row.get("cogs_cents"),
                "bin_code": row.get("bin_code"),
                "floor_price_cents": row.get("floor_price_cents"),
                "estimated_fees_cents": econ.estimated_fees_cents,
                "estimated_net_payout_cents": econ.estimated_net_payout_cents,
                "plg_5pct_net_payout_cents": econ_plg5.estimated_net_payout_cents,
                "plg_5pct_net_profit_cents": econ_plg5.net_profit_cents,
                "very_high_inad_risk_cents": econ.very_high_inad_risk_cents,
                "net_profit_cents": econ.net_profit_cents,
                "margin_pct": econ.margin_pct,
                "qa_score": qa_score,
                "qa_flags": qa_flags,
                "compliance_warnings": [*comp_rep.warnings, *dim_check.warnings],
                "comp_band": comp_bands.get(sku),
                "pending_actions": pending_by_sku.get(sku, []),
                "listed_at": row.get("listed_at"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            items_out.append(enriched)
            items_by_sku[sku] = enriched
            if row.get("ebay_item_id"):
                items_by_ebay_id[str(row["ebay_item_id"])] = enriched

        # Attach item titles onto pending_actions for convenient rendering
        for p in active_pending:
            linked = items_by_sku.get(str(p.get("sku") or ""))
            if p["action_type"] == "BULK_PUBLISH":
                batch_items = p["payload"].get("items") or []
                src_fmt = p["payload"].get("source_format") or "CSV"
                p["item_title"] = f"Bulk Migration Batch ({len(batch_items)} SKUs · {src_fmt})"
                p["list_price_cents"] = sum(int(x.get("list_price_cents") or 0) for x in batch_items)
            else:
                p["item_title"] = linked["title"] if linked else (p["payload"].get("title") or p["action_type"])
                p["list_price_cents"] = linked["list_price_cents"] if linked else p["payload"].get("list_price_cents")

        # Offers & Paid Orders: use tour extras when touring, or mock adapter when adapter_mode == mock
        raw_offers: list[dict[str, Any]] = []
        raw_orders: list[dict[str, Any]] = []
        if tour_extras is not None:
            raw_offers = list(tour_extras.get("offers") or [])
            raw_orders = list(tour_extras.get("paid_orders") or [])
        elif settings.adapter_mode == "mock":
            mock_ad = adapter_mod.get_adapter(settings)
            raw_offers = [o.model_dump(mode="json") for o in mock_ad.list_best_offers()]
            raw_orders = [o.model_dump(mode="json") for o in mock_ad.list_paid_orders(50)]

        # Join offers with local item economics and any staged RESPOND_OFFER tokens
        staged_offer_tokens = {
            str(p["payload"].get("best_offer_id") or ""): p
            for p in active_pending
            if p["action_type"] == "RESPOND_OFFER"
        }
        offers_out: list[dict[str, Any]] = []
        for off in raw_offers:
            linked = (
                items_by_sku.get(str(off.get("sku") or ""))
                or items_by_ebay_id.get(str(off.get("ebay_item_id") or ""))
            )
            offer_price = int(off.get("price_cents") or off.get("offer_price_cents") or 0)
            buyer_name = str(off.get("buyer_username") or off.get("buyer_masked_id") or "buyer")
            cogs = linked.get("cogs_cents") if linked else None
            floor_p = linked.get("floor_price_cents") if linked else None
            list_p = linked.get("list_price_cents") if linked else offer_price
            off_econ = tools_mod.compute_economics(
                list_price_cents=offer_price,
                shipping_est_cents=int(linked.get("shipping_est_cents") or 0) if linked else 0,
                estimated_fee_bps=fee_bps,
                cogs_cents=cogs,
            )
            status_str = str(off["status"])
            is_awaiting_payment = status_str == "SellerAccept"
            meets_floor = bool(floor_p is not None and offer_price >= int(floor_p))
            rec = (
                "AWAITING_PAYMENT"
                if is_awaiting_payment
                else ("ACCEPT" if meets_floor else ("COUNTER" if floor_p else "REVIEW"))
            )
            staged_tok = staged_offer_tokens.get(str(off["best_offer_id"]))
            offers_out.append(
                {
                    **off,
                    "price_cents": offer_price,
                    "buyer_username": buyer_name,
                    "title": linked["title"] if linked else (off.get("title") or f"eBay Item {off.get('ebay_item_id')}"),
                    "sku": linked["sku"] if linked else off.get("sku"),
                    "bin_code": linked.get("bin_code") if linked else None,
                    "list_price_cents": list_p,
                    "floor_price_cents": floor_p,
                    "cogs_cents": cogs,
                    "estimated_net_payout_cents": off_econ.estimated_net_payout_cents,
                    "net_profit_cents": off_econ.net_profit_cents,
                    "margin_pct": off_econ.margin_pct,
                    "is_awaiting_payment": is_awaiting_payment,
                    "meets_floor": meets_floor,
                    "recommendation": rec,
                    "staged_action_token": staged_tok["action_token"] if staged_tok else None,
                }
            )

        # Build naturally sorted warehouse bin pick-list
        packed_ids = db_mod.get_packed_order_ids(settings.plugin_name)
        pick_list: list[dict[str, Any]] = []
        for ord_row in raw_orders:
            linked = (
                items_by_sku.get(str(ord_row.get("sku") or ""))
                or items_by_ebay_id.get(str(ord_row.get("ebay_item_id") or ""))
            )
            bin_code = (linked.get("bin_code") if linked else None) or "UNASSIGNED"
            oid = str(ord_row["order_id"])
            total_cents = int(ord_row.get("total_cents") or ord_row.get("sold_price_cents") or 0)
            pick_list.append(
                {
                    "order_id": oid,
                    "sku": (linked["sku"] if linked else ord_row.get("sku")) or "UNLINKED",
                    "ebay_item_id": ord_row.get("ebay_item_id"),
                    "title": (linked["title"] if linked else ord_row.get("title")) or "Sold Item",
                    "bin_code": bin_code,
                    "quantity": int(ord_row.get("quantity") or 1),
                    "total_cents": total_cents,
                    "buyer_username": ord_row.get("buyer_username") or "buyer",
                    "paid_at": ord_row.get("paid_at"),
                    "package_weight_oz": linked.get("package_weight_oz") if linked else None,
                    "packed": oid in packed_ids,
                }
            )
        pick_list.sort(key=lambda r: (_natural_bin_key(r["bin_code"]), r["order_id"]))

        # Recent audit events
        raw_events = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM item_events ORDER BY event_id DESC LIMIT 45"
            ).fetchall()
        ]
        events_week_count = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM item_events WHERE created_at >= ?",
                (week_ago_iso,),
            ).fetchone()["n"]
        )
        activity_out: list[dict[str, Any]] = []
        for ev in raw_events:
            detail = json.loads(ev.get("detail_json") or "{}")
            linked = items_by_sku.get(str(ev["sku"]))
            activity_out.append(
                {
                    "event_id": ev["event_id"],
                    "sku": ev["sku"],
                    "title": linked["title"] if linked else ev["sku"],
                    "event_type": ev["event_type"],
                    "actor": ev["actor"],
                    "detail": detail,
                    "created_at": ev["created_at"],
                }
            )

        staged_items = [i for i in items_out if i["status"] == "STAGED"]
        active_items = [i for i in items_out if i["status"] == "ACTIVE"]
        sold_items = [i for i in items_out if i["status"] == "SOLD"]
        active_offers = [o for o in offers_out if not o["is_awaiting_payment"]]
        awaiting_payment_offers = [o for o in offers_out if o["is_awaiting_payment"]]

        total_entered = max(1, len(items_out))
        qa_ready_count = sum(1 for i in items_out if i["qa_score"] >= 85)
        published_or_sold = len(active_items) + len(sold_items)
        engaged_count = len(offers_out) + len(sold_items)

        funnel = [
            {
                "id": "captured",
                "label": "Captured",
                "count": len(items_out),
                "value_cents": staged_value_cents + active_value_cents + sold_value_cents,
                "sub": f"{len(staged_items)} staged now",
            },
            {
                "id": "qa_verified",
                "label": "QA Verified",
                "count": qa_ready_count,
                "value_cents": sum(i["list_price_cents"] for i in items_out if i["qa_score"] >= 85),
                "sub": f"{round(100 * qa_ready_count / total_entered)}% anti-INAD pass",
            },
            {
                "id": "active",
                "label": "Live on eBay",
                "count": len(active_items),
                "value_cents": active_value_cents,
                "sub": "Inventory API" if profile.get("policies_opted_in") else "C2C Trading fallback",
            },
            {
                "id": "offers",
                "label": "In Negotiation",
                "count": len(offers_out),
                "value_cents": sum(o["price_cents"] for o in offers_out),
                "sub": f"{len(awaiting_payment_offers)} awaiting payment",
            },
            {
                "id": "paid",
                "label": "Paid & Ship" if not pro_active else "Paid & Pick",
                "count": len(pick_list) or len(sold_items),
                "value_cents": sum(p["total_cents"] for p in pick_list) or sold_value_cents,
                "sub": (
                    f"{sum(1 for p in pick_list if not p['packed'])} to pull from bins"
                    if pro_active
                    else f"{sum(1 for p in pick_list if not p['packed'])} paid · ready to ship"
                ),
            },
        ]

        data_dir = storage_mod.plugin_data_dir(settings.plugin_name)
        db_file_path = str(data_dir / ("tour.db" if tour else "data.db"))

        stale_diagnostics = diagnostics_mod.analyze_stale_listings(items_out)
        hauls = cache_mod.list_haul_summaries(items_out, plugin_name=settings.plugin_name)
        aspect_cache_stats = cache_mod.get_aspect_cache_stats(settings.plugin_name)

        return {
            "ok": True,
            "plugin": settings.plugin_name,
            "mode": "pro" if pro_active else "lite",
            "adapter_mode": "tour" if tour else settings.adapter_mode,
            "desktop_live": (time.time() - _DESKTOP_HEARTBEAT_AT) < 90,
            "tour": tour,
            "tours": demo_mod.get_tour_catalog(),
            "profile": {
                "marketplace_id": profile["marketplace_id"],
                "policies_opted_in": bool(profile["policies_opted_in"]),
                "pro_ops_enabled": pro_active,
                "estimated_fee_bps": fee_bps,
                "merchant_location_key": profile.get("merchant_location_key"),
                "fulfillment_policy_id": profile.get("fulfillment_policy_id"),
                "return_policy_id": profile.get("return_policy_id"),
                "route_label": (
                    "Sell Inventory REST API (Business Policies)"
                    if profile["policies_opted_in"]
                    else "Casual C2C Fallback + Seller Hub CRLF Draft CSV"
                ),
                "db_path": db_file_path,
            },
            "totals": {
                "total_items": len(items_out),
                "staged_count": len(staged_items),
                "active_count": len(active_items),
                "sold_count": len(sold_items),
                "active_value_cents": active_value_cents,
                "staged_value_cents": staged_value_cents,
                "sold_value_cents": sold_value_cents,
                "est_fees_total_cents": est_fees_total_cents,
                "est_net_proceeds_cents": est_net_proceeds_cents,
                "cogs_total_cents": cogs_total_cents,
                "potential_profit_cents": potential_profit_cents,
                "avg_margin_pct": round(sum(margins) / len(margins), 1) if margins else None,
                "pending_approvals_count": len(active_pending),
                "qa_alerts_count": qa_alerts_count,
                "stale_count": len(stale_diagnostics),
                "active_offers_count": len(active_offers),
                "awaiting_payment_offers_count": len(awaiting_payment_offers),
                "pick_list_count": len(pick_list),
                "unpacked_pick_count": sum(1 for p in pick_list if not p["packed"]),
                "events_week_count": events_week_count,
                "published_rate_pct": round(100 * published_or_sold / total_entered) if items_out else 0,
                "engaged_count": engaged_count,
            },
            "funnel": funnel,
            "items": items_out,
            "pending_actions": active_pending,
            "offers": offers_out,
            "pick_list": pick_list,
            "activity": activity_out,
            "stale_diagnostics": stale_diagnostics,
            "hauls": hauls,
            "aspect_cache_stats": aspect_cache_stats,
            "stocked_boxes": list(tools_mod.STOCKED_BOX_INVENTORY),
            "cron_blueprints": list(scripts_mod.CRON_BLUEPRINTS),
            "playbooks": [
                {"id": "ebay-seller-ops", "label": "Core Seller Ops", "rule": "Snap-and-Speak intake, CRLF Draft CSV & two-step gate"},
                {"id": "ebay-apparel-footwear", "label": "Apparel & Footwear", "rule": "Aug 2026 Size Standardization, 2990/3000/3010 & $200-$500 ship guard"},
                {"id": "ebay-motors-fitment", "label": "Motors Parts & Fitment", "rule": "OEM MPN front-load, YMM table (myFitment replacement) & >$10 free returns"},
                {"id": "ebay-vintage-collectibles", "label": "Vintage & Collectibles", "rule": "Catalog-Number Defense (no mold/box # in MPN) & search_by_image"},
                {"id": "ebay-cards-coins-media", "label": "Cards, Coins & Media", "rule": "PSA/PCGS cert check, May 2026 coin grading & Standard Envelope"},
            ],
            "partner_milestones": {
                "mcp_package": "@ebay/npm-public-api-mcp",
                "mcp_config_snippet": 'mcp_servers:\n  ebay_specs:\n    command: "npx"\n    args: ["-y", "@ebay/npm-public-api-mcp"]',
                "growth_check_note": "Default Taxonomy/Browse quota is 5,000 calls/day (cached 7d in cache.db). Request free Application Growth Check in eBay Developer Portal to scale higher.",
                "partner_cutoff_date": "2026-12-31",
                "partner_cutoff_note": "Multi-seller commercial/partner integrations must submit eBay Partner Program applications before December 31, 2026.",
            },
            "setup": {
                "steps": [
                    {
                        "done": bool(items_out),
                        "title": "Stage your first listing or export a Seller Hub CRLF Draft CSV",
                        "detail": "Drop photos into chat, click + New Draft, or export a Windows CRLF Action=Draft CSV (75-day Seller Hub Drafts).",
                    },
                    {
                        "done": settings.adapter_mode == "mock" or bool(tour),
                        "title": "Connect your eBay seller account via Hermes OAuth",
                        "detail": "Run `hermes auth add hermes-ebay-ops:ebay` — credentials stay inside Hermes's encrypted broker.",
                    },
                    {
                        "done": bool(profile["policies_opted_in"]),
                        "title": "Routing & Policy-ID Lock auto-detected",
                        "detail": (
                            f"Active route: {'Sell Inventory REST + Bulk 25 SKUs/call' if profile['policies_opted_in'] else 'Casual C2C AddFixedPriceItem + CRLF Draft CSV'}."
                        ),
                    },
                    {
                        "done": pro_active,
                        "title": "Progressive Pro Ops + inkFrog / myFitment CSV Importer",
                        "detail": (
                            "Unlocked! Offer PLG triage, warehouse bins, 90-180d Cassini diagnostics, and CSV migration are live."
                            if pro_active
                            else "Clean Lite mode active. Add COGS, floor price, or a bin code to unlock Pro tools automatically."
                        ),
                    },
                    {
                        "done": aspect_cache_stats["cached_categories"] > 0,
                        "title": "Taxonomy Quota Cache & @ebay/npm-public-api-mcp Pairing",
                        "detail": (
                            f"{aspect_cache_stats['cached_categories']} leaf category specs cached locally (5,000/day quota guard). "
                            "Optional read-only OpenAPI spec MCP + Dec 31, 2026 Partner cutoff tracked below."
                        ),
                    },
                ],
                "rules": [
                    {
                        "title": "Cryptographic Two-Step Write Gate (15-min TTL + SHA-256)",
                        "detail": "Single publishes, revisions, offer responses, and <=25 SKU bulk batches execute zero live writes without Approve.",
                    },
                    {
                        "title": "July 2026 USPS 139-DIM & Jan 2026 PLG Margin Math",
                        "detail": "Parcels >1,728 cu in check the 139 DIM divisor; pricing models 30-day PLG ad attribution, ~7% sales-tax FVF basis & +5% Very High INAD risk.",
                    },
                    {
                        "title": "Aug/May/Nov 2026 Category & Content Compliance",
                        "detail": "Pre-screens apparel Size Standardization, Vintage Catalog-Number Defense, May 2026 coin grades, duplicate active titles & VeRO terms.",
                    },
                    {
                        "title": "March 2026 SellerAccept & Zero-Token (--no-agent) Cron",
                        "detail": "SellerAccept offers stay live until checkout (flagged at 96h); scheduled checks output [SILENT] at $0 LLM token cost.",
                    },
                ],
            },
        }
    finally:
        conn.close()


@router.post("/confirm")
def confirm_action(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    token = str(body.get("action_token") or "").strip()
    decision = str(body.get("decision") or "").strip().upper()
    if not token or decision not in {"APPROVE", "CANCEL"}:
        raise HTTPException(status_code=400, detail="Provide action_token and decision ('APPROVE' or 'CANCEL').")

    config_mod = _submod("config")
    db_mod = _submod("db")
    phase1_mod = _submod("tools.phase1_mvp")

    prev_settings = config_mod.get_settings()
    tour = db_mod.get_active_tour_info(prev_settings.plugin_name)
    try:
        if tour:
            # Sample-data tours execute offline against MockEbayAdapter inside tour.db
            patched = prev_settings.model_dump()
            patched["adapter_mode"] = "mock"
            patched["mock_persona"] = tour.get("persona", "pro")
            config_mod.configure_settings(patched)
        raw = phase1_mod.handle_confirm_action({"action_token": token, "decision": decision})
        res = json.loads(raw)
        if not res.get("ok"):
            raise HTTPException(status_code=400, detail=res.get("message") or "Confirmation failed")
        return res
    finally:
        if tour:
            config_mod.configure_settings(prev_settings)


@router.post("/stage")
def stage_draft(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    config_mod = _submod("config")
    db_mod = _submod("db")
    phase1_mod = _submod("tools.phase1_mvp")

    notes = str(body.get("seller_notes") or "").strip()
    title_hint = str(body.get("title_hint") or "").strip() or None
    if len(notes) < 3:
        raise HTTPException(status_code=400, detail="Enter a brief description or condition/measurement note.")

    media_url = str(body.get("media_url") or "").strip() or "https://i.ebayimg.com/images/g/vendere-staged-draft/s-l1600.jpg"
    params: dict[str, Any] = {
        "media_paths": [media_url],
        "seller_notes": notes,
    }
    if title_hint:
        params["title_hint"] = title_hint
    for int_field in ("price_cents", "cogs_cents", "floor_price_cents", "plg_ad_rate_bps"):
        val = body.get(int_field)
        if val not in (None, ""):
            params[int_field] = int(val)
    if body.get("package_weight_oz") not in (None, ""):
        params["package_weight_oz"] = float(body["package_weight_oz"])
    raw_dims = body.get("package_dims")
    if isinstance(raw_dims, dict):
        l_v, w_v, h_v = raw_dims.get("length_in"), raw_dims.get("width_in"), raw_dims.get("height_in")
        if all(x not in (None, "", 0, "0") for x in (l_v, w_v, h_v)):
            params["package_dims"] = {
                "length_in": float(l_v),
                "width_in": float(w_v),
                "height_in": float(h_v),
            }
    if body.get("bin_code"):
        params["bin_code"] = str(body["bin_code"]).strip()
    if body.get("condition"):
        params["condition"] = str(body["condition"]).strip()

    prev_settings = config_mod.get_settings()
    tour = db_mod.get_active_tour_info(prev_settings.plugin_name)
    try:
        if tour:
            patched = prev_settings.model_dump()
            patched["adapter_mode"] = "mock"
            patched["mock_persona"] = tour.get("persona", "pro")
            config_mod.configure_settings(patched)
        raw = phase1_mod.handle_stage_listing(params, platform="webapp")
        res = json.loads(raw)
        if not res.get("ok"):
            raise HTTPException(status_code=400, detail=res.get("message") or "Could not stage draft")
        return res
    finally:
        if tour:
            config_mod.configure_settings(prev_settings)


@router.post("/item/{sku}/enrich")
def enrich_item(sku: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    db_mod = _submod("db")
    models_mod = _submod("models")
    tools_mod = _submod("tools")

    conn = db_mod.get_db()
    try:
        existing = db_mod.get_item_by_sku(conn, sku)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Unknown SKU: {sku}")

        def _opt_int(key: str, current: Any) -> int | None:
            if key not in body:
                return current
            v = body[key]
            return None if v in (None, "") else int(v)

        cogs_cents = _opt_int("cogs_cents", existing.get("cogs_cents"))
        floor_price_cents = _opt_int("floor_price_cents", existing.get("floor_price_cents"))
        list_price_cents = _opt_int("list_price_cents", existing["list_price_cents"]) or int(existing["list_price_cents"])
        bin_code = (
            (str(body["bin_code"]).strip() or None)
            if "bin_code" in body
            else existing.get("bin_code")
        )
        weight_oz = (
            (None if body["package_weight_oz"] in (None, "") else float(body["package_weight_oz"]))
            if "package_weight_oz" in body
            else existing.get("package_weight_oz")
        )
        flaws = (
            str(body["flaws_disclosure"]).strip()
            if "flaws_disclosure" in body
            else existing.get("flaws_disclosure")
        )
        if "package_dims" in body:
            raw_dims = body.get("package_dims")
            if isinstance(raw_dims, dict) and all(raw_dims.get(k) not in (None, "", 0, "0") for k in ("length_in", "width_in", "height_in")):
                pkg_dims = {
                    "length_in": float(raw_dims["length_in"]),
                    "width_in": float(raw_dims["width_in"]),
                    "height_in": float(raw_dims["height_in"]),
                }
            else:
                pkg_dims = None
        else:
            pkg_dims = json.loads(existing["package_dims_json"]) if existing.get("package_dims_json") else None

        pro_unlocked = db_mod.maybe_enable_pro_ops(
            conn,
            cogs_cents=cogs_cents,
            bin_code=bin_code,
            floor_price_cents=floor_price_cents,
        )
        db_mod.upsert_item(
            conn,
            sku=sku,
            ebay_item_id=existing.get("ebay_item_id"),
            offer_id=existing.get("offer_id"),
            source_mode=existing["source_mode"],
            status=existing["status"],
            title=str(body.get("title") or existing["title"]),
            category_id=existing["category_id"],
            condition_enum=existing["condition_enum"],
            list_price_cents=list_price_cents,
            shipping_est_cents=int(existing.get("shipping_est_cents") or 0),
            package_weight_oz=weight_oz,
            package_dims=pkg_dims,
            aspects=json.loads(existing.get("aspects_json") or "{}"),
            flaws_disclosure=flaws,
            media_urls=json.loads(existing.get("media_urls_json") or "[]"),
            cogs_cents=cogs_cents,
            bin_code=bin_code,
            floor_price_cents=floor_price_cents,
            listed_at=existing.get("listed_at"),
        )
        db_mod.append_item_event(
            conn,
            sku=sku,
            event_type="ITEM_ENRICHED",
            actor=models_mod.EventActor.USER,
            detail={
                "cogs_cents": cogs_cents,
                "floor_price_cents": floor_price_cents,
                "bin_code": bin_code,
                "pro_ops_enabled": pro_unlocked,
            },
        )
        tools_mod.announce_change()
        return {"ok": True, "sku": sku, "pro_ops_enabled": pro_unlocked}
    finally:
        conn.close()


@router.post("/offer/stage")
def stage_offer_decision(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    db_mod = _submod("db")
    models_mod = _submod("models")
    tools_mod = _submod("tools")

    best_offer_id = str(body.get("best_offer_id") or "").strip()
    ebay_item_id = str(body.get("ebay_item_id") or "").strip()
    sku = str(body.get("sku") or "").strip() or None
    action_str = str(body.get("action") or "").strip()
    counter_cents = int(body["counter_price_cents"]) if body.get("counter_price_cents") not in (None, "") else None
    msg = str(body.get("seller_message") or "").strip() or None

    try:
        payload = models_mod.RespondOfferPayload(
            sku=sku,
            ebay_item_id=ebay_item_id,
            best_offer_id=best_offer_id,
            action=models_mod.OfferDecisionAction(action_str),
            counter_price_cents=counter_cents,
            seller_message=msg,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    conn = db_mod.get_db()
    try:
        card_md = (
            f"### Staged Best Offer Response — `{action_str}` on `{best_offer_id}`\n"
            + (f"- **Counter Price**: {tools_mod.format_usd(counter_cents)}\n" if counter_cents else "")
            + "- **Gate**: Requires explicit human approval before live eBay execution."
        )
        rec = db_mod.stage_pending_action(
            conn,
            action_type=models_mod.ActionType.RESPOND_OFFER,
            sku=sku if (sku and db_mod.get_item_by_sku(conn, sku)) else None,
            payload=payload.model_dump(mode="json"),
            preview_card_md=card_md,
        )
        tools_mod.announce_change()
        return {"ok": True, "action_token": rec.action_token, "expires_at": rec.expires_at}
    finally:
        conn.close()


@router.post("/pick/{order_id}/toggle")
def toggle_pick_line(order_id: str) -> dict[str, Any]:
    db_mod = _submod("db")
    tools_mod = _submod("tools")
    packed = db_mod.toggle_packed_order_id(order_id)
    tools_mod.announce_change()
    return {"ok": True, "order_id": order_id, "packed": packed}


@router.post("/tour")
def switch_tour(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    demo_mod = _submod("demo")
    tools_mod = _submod("tools")
    action = str(body.get("action") or "").strip().lower()
    if action == "leave":
        demo_mod.leave_tour()
        tools_mod.announce_change()
        return {"ok": True, "tour": None}
    tour_id = str(body.get("tour_id") or "vault").strip().lower()
    try:
        meta = demo_mod.start_tour(tour_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    tools_mod.announce_change()
    return {"ok": True, "tour": meta}


@router.post("/export/draft-csv")
def export_draft_csv(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    config_mod = _submod("config")
    db_mod = _submod("db")
    phase1_mod = _submod("tools.phase1_mvp")

    prev_settings = config_mod.get_settings()
    tour = db_mod.get_active_tour_info(prev_settings.plugin_name)
    try:
        if tour:
            patched = prev_settings.model_dump()
            patched["adapter_mode"] = "mock"
            patched["mock_persona"] = tour.get("persona", "pro")
            config_mod.configure_settings(patched)
        raw = phase1_mod.handle_export_draft_csv(body or {}, platform="webapp")
        res = json.loads(raw)
        if not res.get("ok"):
            raise HTTPException(status_code=400, detail=res.get("message") or "Could not export CRLF Draft CSV")
        res["filename"] = "vendere-seller-hub-drafts.csv"
        return res
    finally:
        if tour:
            config_mod.configure_settings(prev_settings)


@router.post("/import/csv")
def import_migration_csv(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    config_mod = _submod("config")
    db_mod = _submod("db")
    phase2_mod = _submod("tools.phase2_ops")

    prev_settings = config_mod.get_settings()
    tour = db_mod.get_active_tour_info(prev_settings.plugin_name)
    try:
        if tour:
            patched = prev_settings.model_dump()
            patched["adapter_mode"] = "mock"
            patched["mock_persona"] = tour.get("persona", "pro")
            config_mod.configure_settings(patched)
        raw = phase2_mod.handle_import_migration_csv(body or {}, platform="webapp")
        res = json.loads(raw)
        if not res.get("ok"):
            raise HTTPException(status_code=400, detail=res.get("message") or "Could not import CSV batch")
        return res
    finally:
        if tour:
            config_mod.configure_settings(prev_settings)


@router.post("/haul")
def upsert_haul(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    config_mod = _submod("config")
    cache_mod = _submod("cache_store")
    tools_mod = _submod("tools")

    label = str(body.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="Provide a non-empty haul batch label.")
    haul = cache_mod.upsert_haul_batch(
        haul_id=str(body["haul_id"]).strip() if body.get("haul_id") else None,
        label=label,
        total_cost_cents=int(body.get("total_cost_cents") or 0),
        item_count=max(1, int(body.get("item_count") or 1)),
        unlisted_pile_count=max(0, int(body.get("unlisted_pile_count") or 0)),
        daily_sprint_target=max(1, int(body.get("daily_sprint_target") or 3)),
        notes=str(body["notes"]).strip() if body.get("notes") else None,
        plugin_name=config_mod.get_settings().plugin_name,
    )
    tools_mod.announce_change()
    return {"ok": True, "haul": haul}


@router.post("/cron/{job_id}/run")
def run_zero_token_cron(job_id: str, body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    config_mod = _submod("config")
    db_mod = _submod("db")
    scripts_mod = _submod("scripts")

    prev_settings = config_mod.get_settings()
    tour = db_mod.get_active_tour_info(prev_settings.plugin_name)
    try:
        if tour:
            patched = prev_settings.model_dump()
            patched["adapter_mode"] = "mock"
            patched["mock_persona"] = tour.get("persona", "pro")
            config_mod.configure_settings(patched)
        question = str(body["question"]).strip() if body and body.get("question") else None
        res = scripts_mod.run_cron_job(job_id, sample_question=question)
        return {"ok": True, **res}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        if tour:
            config_mod.configure_settings(prev_settings)
