"""Phase 2 Pro reseller tools (gated via check_fn=is_pro_ops_enabled):
- ebay_triage_offers
- ebay_lookup_bin
- ebay_import_migration_csv
- ebay_diagnose_stale
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from hermes_cli.plugin_provider_requests import ProviderNotSignedIn
from pydantic import ValidationError

from ..adapter import EbayApiError, get_adapter
from ..cache_store import list_haul_summaries
from ..compliance import screen_listing_compliance
from ..config import NOT_CONNECTED_MESSAGE, get_settings
from ..csv_drafts import parse_migration_csv
from ..db import (
    append_item_event,
    get_db,
    get_item_by_ebay_item_id,
    get_item_by_sku,
    get_or_create_seller_profile,
    maybe_enable_pro_ops,
    stage_pending_action,
    update_seller_capabilities,
    upsert_item,
)
from ..diagnostics import analyze_stale_listings, detect_unpaid_96h_offers
from ..models import (
    ActionType,
    BestOfferLifecycleStatus,
    BulkPublishPayload,
    DiagnoseStaleInput,
    EventActor,
    ImportMigrationCsvInput,
    ItemStatus,
    LookupBinInput,
    OfferDecisionAction,
    RespondOfferPayload,
    SourceMode,
    StagedListingPayload,
    TriageOffersInput,
)
from . import compute_economics, format_tool_response, format_usd

SCHEMA_TRIAGE_OFFERS: dict[str, Any] = {
    "name": "ebay_triage_offers",
    "description": (
        "Pro reseller Best Offer triage: fetches Best Offers via eBay Trading GetBestOffers, "
        "filters out unpaid 2026 SellerAccept offers awaiting buyer checkout so they are never double-actioned, "
        "joins Active offers against local COGS and floor_price_cents, computes net profit and margin % after fees, "
        "and stages single-use RESPOND_OFFER action_tokens for Accept or Counter decisions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ebay_item_id": {
                "type": "string",
                "description": "Optional eBay Item ID to filter Best Offers to a single listing.",
            },
            "min_margin_bps": {
                "type": "integer",
                "description": "Minimum target net profit margin in basis points when floor_price_cents is unset (default 1500 = 15.0%).",
            },
            "plg_ad_rate_bps": {
                "type": "integer",
                "description": "Optional Jan 2026 Promoted Listings General ad rate in basis points (e.g. 500 for 5.0%) deducted from net profit.",
            },
        },
        "required": [],
    },
}

SCHEMA_LOOKUP_BIN: dict[str, Any] = {
    "name": "ebay_lookup_bin",
    "description": (
        "Read-only Pro warehouse fulfillment helper: queries paid unshipped orders via eBay REST Sell Fulfillment API, "
        "joins sold SKUs and item IDs against local SQLite bin_code locations, and returns a bin-sorted warehouse pick-list."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of paid orders to retrieve (1-100, default 25).",
            },
        },
        "required": [],
    },
}

SCHEMA_IMPORT_MIGRATION_CSV: dict[str, Any] = {
    "name": "ebay_import_migration_csv",
    "description": (
        "One-step migration importer for displaced sellers moving off shuttered inkFrog (June 1, 2026), "
        "myFitment (Sept 30, 2026), or Seller Hub CSV exports. Strips broken inkFrog HTML wrappers, aggregates "
        "multi-row Year/Make/Model/Trim/Engine vehicle fitment, stages normalized drafts in SQLite, and mints a "
        "single two-step BULK_PUBLISH action_token (`<=25 SKUs/call` via Sell Inventory REST)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "csv_text": {
                "type": "string",
                "description": "Raw CSV export text from inkFrog, myFitment, or Seller Hub Reports.",
            },
            "source_hint": {
                "type": "string",
                "enum": ["auto", "inkfrog", "myfitment", "seller_hub"],
                "description": "Optional CSV origin hint (defaults to 'auto' header/content detection).",
            },
            "default_condition": {
                "type": "string",
                "description": "Fallback condition enum when a CSV row omits ConditionID (default USED_GOOD).",
            },
        },
        "required": ["csv_text"],
    },
}

SCHEMA_DIAGNOSE_STALE: dict[str, Any] = {
    "name": "ebay_diagnose_stale",
    "description": (
        "Pro 90-180 day Cassini stale-listing & Haul break-even diagnostic. Distinguishes incomplete-aspect "
        "listings that need in-place `IN_PLACE_ASPECT_UPGRADE` revisions from healthy-QA aged listings eligible "
        "for sequential `END_AND_RECREATE_5PCT` gentle pruning (Duplicate Listings Policy safe)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "min_age_days": {
                "type": "integer",
                "description": "Minimum days active before evaluating Cassini staleness (default 90).",
            },
            "daily_prune_cap": {
                "type": "integer",
                "description": "Safe daily sequential end-and-recreate cap (default 5).",
            },
            "prune_discount_bps": {
                "type": "integer",
                "description": "Suggested gentle-prune price reduction in basis points (default 500 = 5.0%).",
            },
        },
        "required": [],
    },
}


def _natural_bin_key(bin_code: str | None) -> tuple[int, list[int | str]]:
    if not bin_code:
        return (1, ["ZZZ_UNASSIGNED"])
    tokens: list[int | str] = [
        int(tok) if tok.isdigit() else tok.upper()
        for tok in re.split(r"(\d+)", bin_code.strip())
        if tok
    ]
    return (0, tokens)


def handle_triage_offers(
    params: dict[str, Any],
    *,
    session_id: str = "",
    platform: str = "",
    **kwargs: Any,
) -> str:
    try:
        inp = TriageOffersInput.model_validate(params or {})
    except ValidationError as exc:
        return format_tool_response({"ok": False, "error_code": "INVALID_INPUT", "message": str(exc)})

    settings = get_settings()
    adapter = get_adapter(settings)
    conn = get_db(settings.plugin_name)
    try:
        profile = get_or_create_seller_profile(conn)
        fee_bps = int(profile["estimated_fee_bps"])
        offers = adapter.list_best_offers(inp.ebay_item_id)

        actionable_results: list[dict[str, Any]] = []
        awaiting_payment: list[dict[str, Any]] = []
        skipped_other: list[dict[str, Any]] = []

        md_lines = ["### Pro Best Offer Triage"]

        for offer in offers:
            if offer.status == BestOfferLifecycleStatus.SELLER_ACCEPT:
                awaiting_payment.append(
                    {
                        "best_offer_id": offer.best_offer_id,
                        "ebay_item_id": offer.ebay_item_id,
                        "offer_price_cents": offer.offer_price_cents,
                        "status": offer.status.value,
                        "note": "Seller accepted; awaiting buyer payment — skipped to prevent double-action.",
                    }
                )
                continue

            if offer.status != BestOfferLifecycleStatus.ACTIVE:
                skipped_other.append(
                    {
                        "best_offer_id": offer.best_offer_id,
                        "ebay_item_id": offer.ebay_item_id,
                        "status": offer.status.value,
                    }
                )
                continue

            local_item = (
                (get_item_by_sku(conn, offer.sku) if offer.sku else None)
                or get_item_by_ebay_item_id(conn, offer.ebay_item_id)
            )
            local_sku = str(local_item["sku"]) if local_item else offer.sku
            cogs_cents = local_item["cogs_cents"] if local_item else None
            floor_cents = local_item["floor_price_cents"] if local_item else None
            list_price_cents = int(local_item["list_price_cents"]) if local_item else None
            ship_cents = int(local_item["shipping_est_cents"]) if local_item else 0

            econ = compute_economics(
                list_price_cents=offer.offer_price_cents,
                shipping_est_cents=ship_cents,
                estimated_fee_bps=fee_bps,
                cogs_cents=cogs_cents,
                plg_ad_rate_bps=inp.plg_ad_rate_bps,
            )

            min_margin_pct = inp.min_margin_bps / 100.0
            should_accept = False
            if floor_cents is not None:
                should_accept = offer.offer_price_cents >= int(floor_cents)
            elif econ.margin_pct is not None:
                should_accept = econ.margin_pct >= min_margin_pct
            elif list_price_cents is not None:
                should_accept = offer.offer_price_cents >= int(round(list_price_cents * 0.85))

            if should_accept:
                rec_action = OfferDecisionAction.ACCEPT
                counter_cents = None
                rationale = (
                    f"Meets floor ({format_usd(int(floor_cents))})"
                    if floor_cents is not None
                    else "Meets target margin threshold"
                )
            else:
                rec_action = OfferDecisionAction.COUNTER
                candidates: list[int] = [offer.offer_price_cents + 500]
                if floor_cents is not None:
                    candidates.append(int(floor_cents))
                if list_price_cents is not None:
                    candidates.append(int(round(list_price_cents * 0.90)))
                raw_counter = max(candidates)
                if list_price_cents is not None:
                    raw_counter = min(raw_counter, list_price_cents)
                counter_cents = max(1, raw_counter)
                rationale = (
                    f"Below floor {format_usd(int(floor_cents))}; counter at {format_usd(counter_cents)}"
                    if floor_cents is not None
                    else f"Below {min_margin_pct:.1f}% margin target; counter at {format_usd(counter_cents)}"
                )

            staged_sku_fk = local_sku if (local_item is not None and local_sku) else None
            respond_payload = RespondOfferPayload(
                sku=staged_sku_fk,
                ebay_item_id=offer.ebay_item_id,
                best_offer_id=offer.best_offer_id,
                action=rec_action,
                counter_price_cents=counter_cents,
            )

            offer_card = (
                f"- **Offer `{offer.best_offer_id}`** on `{offer.ebay_item_id}` ({offer.title or local_sku or 'Item'}): "
                f"Buyer `{offer.buyer_masked_id}` offered **{format_usd(offer.offer_price_cents)}** | "
                f"Net Payout: {format_usd(econ.estimated_net_payout_cents)}"
            )
            if econ.net_profit_cents is not None and econ.margin_pct is not None:
                offer_card += f" | Net Profit: **{format_usd(econ.net_profit_cents)}** ({econ.margin_pct:.1f}%)"
            offer_card += f" -> Recommended: **{rec_action.value}** ({rationale})"

            pending = stage_pending_action(
                conn,
                action_type=ActionType.RESPOND_OFFER,
                sku=staged_sku_fk,
                payload=respond_payload.model_dump(mode="json"),
                preview_card_md=offer_card,
            )
            offer_card_with_token = f"{offer_card} | Token: `{pending.action_token}`"
            md_lines.append(offer_card_with_token)

            if staged_sku_fk:
                append_item_event(
                    conn,
                    sku=staged_sku_fk,
                    event_type="OFFER_TRIAGE_STAGED",
                    actor=EventActor.AGENT_STAGED,
                    detail={
                        "best_offer_id": offer.best_offer_id,
                        "offer_price_cents": offer.offer_price_cents,
                        "recommended_action": rec_action.value,
                        "counter_price_cents": counter_cents,
                        "action_token": pending.action_token,
                        "session_id": session_id,
                        "platform": platform,
                    },
                )

            actionable_results.append(
                {
                    "best_offer_id": offer.best_offer_id,
                    "ebay_item_id": offer.ebay_item_id,
                    "sku": local_sku,
                    "offer_price_cents": offer.offer_price_cents,
                    "estimated_net_payout_cents": econ.estimated_net_payout_cents,
                    "net_profit_cents": econ.net_profit_cents,
                    "margin_pct": econ.margin_pct,
                    "recommended_action": rec_action.value,
                    "counter_price_cents": counter_cents,
                    "action_token": pending.action_token,
                    "expires_at": pending.expires_at,
                }
            )

        if not actionable_results:
            md_lines.append("- No actionable `Active` Best Offers require a seller decision right now.")

        if awaiting_payment:
            md_lines.append("")
            md_lines.append("#### Awaiting Buyer Payment (`SellerAccept` — Do Not Re-Action)")
            for ap in awaiting_payment:
                md_lines.append(
                    f"- `{ap['best_offer_id']}` on `{ap['ebay_item_id']}` ({format_usd(ap['offer_price_cents'])}) — "
                    "Seller accepted; waiting for buyer checkout."
                )

        unpaid_96h = detect_unpaid_96h_offers(awaiting_payment)
        if unpaid_96h:
            md_lines.append("")
            md_lines.append("#### 96-Hour Unpaid Commitment Alert (`>=4 Days` — Eligible to Cancel & Relist)")
            for up in unpaid_96h:
                md_lines.append(
                    f"- `{up['best_offer_id']}` on `{up['ebay_item_id']}` unpaid for **{up['hours_unpaid']}h** -> `{up['recommendation']}`"
                )

        return format_tool_response(
            {
                "ok": True,
                "tool": "ebay_triage_offers",
                "actionable_offers": actionable_results,
                "awaiting_buyer_payment": awaiting_payment,
                "unpaid_96h_flags": unpaid_96h,
                "skipped_non_active": skipped_other,
                "preview_card_md": "\n".join(md_lines),
            }
        )
    except ProviderNotSignedIn as exc:
        return format_tool_response(
            {
                "ok": False,
                "error_code": "AUTH_REQUIRED",
                "message": f"{NOT_CONNECTED_MESSAGE} ({exc})",
            }
        )
    except EbayApiError as exc:
        return format_tool_response({"ok": False, "error_code": "EBAY_API_ERROR", "message": str(exc)})
    finally:
        conn.close()


def handle_import_migration_csv(
    params: dict[str, Any],
    *,
    session_id: str = "",
    platform: str = "",
    **kwargs: Any,
) -> str:
    """Import shuttered inkFrog, myFitment, or Seller Hub CSV exports and stage a <=25 SKU BULK_PUBLISH token."""
    try:
        inp = ImportMigrationCsvInput.model_validate(params or {})
    except ValidationError as exc:
        return format_tool_response({"ok": False, "error_code": "INVALID_INPUT", "message": str(exc)})

    settings = get_settings()
    adapter = get_adapter(settings)
    conn = get_db(settings.plugin_name)
    try:
        detected_fmt, parsed_rows = parse_migration_csv(
            inp.csv_text,
            source_hint=inp.source_hint,
            default_condition=inp.default_condition,
        )
        if not parsed_rows:
            return format_tool_response(
                {"ok": False, "error_code": "EMPTY_CSV", "message": "No valid listing rows found in CSV payload."}
            )

        capabilities = adapter.check_policy_status(settings.marketplace_id)
        update_seller_capabilities(conn, capabilities)
        route_mode: Literal["INVENTORY_REST", "TRADING_XML_FALLBACK"] = (
            "INVENTORY_REST" if capabilities.policies_opted_in else "TRADING_XML_FALLBACK"
        )

        staged_payloads: list[StagedListingPayload] = []
        warnings_summary: list[str] = []

        for row in parsed_rows[:25]:
            compliance = screen_listing_compliance(
                title=row["title"],
                description=row["description"],
                category_id=row["category_id"],
                condition=row["condition"],
                list_price_cents=row["list_price_cents"],
                aspects=row["aspects"],
                media_urls_or_paths=row["media_urls"],
                return_policy_id=capabilities.return_policy_id,
                sku=row["sku"],
                conn=conn,
            )
            for w in compliance.warnings[:2]:
                warnings_summary.append(f"`{row['sku']}`: {w}")

            maybe_enable_pro_ops(conn, cogs_cents=row["cogs_cents"], bin_code=row["bin_code"])
            payload = StagedListingPayload(
                sku=row["sku"],
                title=row["title"],
                description=row["description"],
                category_id=row["category_id"],
                condition=row["condition"],
                list_price_cents=row["list_price_cents"],
                shipping_est_cents=850,
                aspects=compliance.sanitized_aspects,
                media_urls=row["media_urls"],
                marketplace_id=settings.marketplace_id,
                route_mode=route_mode,
                fulfillment_policy_id=capabilities.fulfillment_policy_id,
                payment_policy_id=capabilities.payment_policy_id,
                return_policy_id=capabilities.return_policy_id,
                merchant_location_key=capabilities.merchant_location_key,
                cogs_cents=row["cogs_cents"],
                bin_code=row["bin_code"],
            )
            staged_payloads.append(payload)
            upsert_item(
                conn,
                sku=payload.sku,
                source_mode=SourceMode.AGENT_CAPTURE,
                status=ItemStatus.STAGED,
                title=payload.title,
                category_id=payload.category_id,
                condition_enum=payload.condition,
                list_price_cents=payload.list_price_cents,
                shipping_est_cents=payload.shipping_est_cents,
                aspects=payload.aspects,
                media_urls=payload.media_urls,
                cogs_cents=payload.cogs_cents,
                bin_code=payload.bin_code,
            )
            append_item_event(
                conn,
                sku=payload.sku,
                event_type="MIGRATION_CSV_IMPORTED",
                actor=EventActor.AGENT_STAGED,
                detail={"source_format": detected_fmt, "session_id": session_id},
            )

        bulk_model = BulkPublishPayload(items=staged_payloads, source_format=detected_fmt.upper())
        md_lines = [
            f"### Migrated `{detected_fmt.upper()}` CSV Batch — **{len(staged_payloads)}** Draft(s) Staged",
            f"- **Bulk Route**: `{route_mode}` (`<=25 SKUs/call` via Sell Inventory Bulk REST when Business Policies active)",
            f"- **SKUs**: {', '.join(f'`{p.sku}`' for p in staged_payloads[:10])}",
        ]
        for w in warnings_summary[:5]:
            md_lines.append(f"- **2026 Playbook Pre-Screen**: {w}")

        pending = stage_pending_action(
            conn,
            action_type=ActionType.BULK_PUBLISH,
            sku=staged_payloads[0].sku,
            payload=bulk_model.model_dump(mode="json"),
            preview_card_md="\n".join(md_lines),
        )
        final_card = "\n".join(md_lines) + f"\n- **Bulk Approval Token**: `{pending.action_token}` *(expires {pending.expires_at})*"
        conn.execute(
            "UPDATE pending_actions SET preview_card_md = ? WHERE action_token = ?",
            (final_card, pending.action_token),
        )
        conn.commit()

        return format_tool_response(
            {
                "ok": True,
                "tool": "ebay_import_migration_csv",
                "detected_format": detected_fmt,
                "imported_count": len(staged_payloads),
                "skus": [p.sku for p in staged_payloads],
                "compliance_warnings": warnings_summary,
                "action_token": pending.action_token,
                "expires_at": pending.expires_at,
                "preview_card_md": final_card,
            }
        )
    except ProviderNotSignedIn as exc:
        return format_tool_response(
            {
                "ok": False,
                "error_code": "AUTH_REQUIRED",
                "message": f"{NOT_CONNECTED_MESSAGE} ({exc})",
            }
        )
    except EbayApiError as exc:
        return format_tool_response({"ok": False, "error_code": "EBAY_API_ERROR", "message": str(exc)})
    finally:
        conn.close()


def handle_diagnose_stale(
    params: dict[str, Any],
    *,
    session_id: str = "",
    platform: str = "",
    **kwargs: Any,
) -> str:
    """Evaluate 90-180d Cassini listing decay and Haul / Death-Pile break-even progress."""
    try:
        inp = DiagnoseStaleInput.model_validate(params or {})
    except ValidationError as exc:
        return format_tool_response({"ok": False, "error_code": "INVALID_INPUT", "message": str(exc)})

    settings = get_settings()
    conn = get_db(settings.plugin_name)
    try:
        profile = get_or_create_seller_profile(conn)
        fee_bps = int(profile["estimated_fee_bps"])
        raw_items = [dict(r) for r in conn.execute("SELECT * FROM items ORDER BY updated_at DESC").fetchall()]
        for it in raw_items:
            try:
                it["aspects"] = json.loads(it.get("aspects_json") or "{}")
            except Exception:
                it["aspects"] = {}
            econ = compute_economics(
                list_price_cents=int(it["list_price_cents"]),
                shipping_est_cents=int(it["shipping_est_cents"] or 0),
                estimated_fee_bps=fee_bps,
                cogs_cents=it.get("cogs_cents"),
            )
            it["estimated_net_payout_cents"] = econ.estimated_net_payout_cents

        stale = analyze_stale_listings(
            raw_items,
            min_age_days=inp.min_age_days,
            daily_prune_cap=inp.daily_prune_cap,
            prune_discount_bps=inp.prune_discount_bps,
        )
        hauls = list_haul_summaries(raw_items, plugin_name=settings.plugin_name)

        md_lines = [
            f"### Cassini Stale-Listing Diagnostics (`>={inp.min_age_days}d`, {len(stale)} flagged)",
        ]
        if not stale:
            md_lines.append("- Zero active listings exceed the staleness threshold.")
        else:
            for row in stale:
                md_lines.append(
                    f"- **`{row['sku']}`** ({row['age_days']}d · `{row['playbook']}`) -> **{row['strategy']}** "
                    f"({format_usd(row['current_price_cents'])} -> {format_usd(row['suggested_price_cents'])}): {row['reason']}"
                )
        if hauls:
            md_lines.append("")
            md_lines.append("#### Haul & Death-Pile Break-Even Velocity")
            for h in hauls[:3]:
                md_lines.append(
                    f"- **{h['label']}**: {h['listed_count']}/{h['item_count']} listed ({h['pct_listed']}%) · "
                    f"Remaining Break-Even: **{format_usd(h['remaining_breakeven_cents'])}** · "
                    f"Next {h['sprint_items']}-item sprint (~{h['sprint_minutes']}m) unlocks ~{format_usd(h['sprint_ebay_balance_unlock_cents'])} eBay Balance."
                )

        return format_tool_response(
            {
                "ok": True,
                "tool": "ebay_diagnose_stale",
                "stale_count": len(stale),
                "stale_listings": stale,
                "hauls": hauls,
                "preview_card_md": "\n".join(md_lines),
            }
        )
    finally:
        conn.close()


def handle_lookup_bin(
    params: dict[str, Any],
    *,
    session_id: str = "",
    platform: str = "",
    **kwargs: Any,
) -> str:
    try:
        inp = LookupBinInput.model_validate(params or {})
    except ValidationError as exc:
        return format_tool_response({"ok": False, "error_code": "INVALID_INPUT", "message": str(exc)})

    settings = get_settings()
    adapter = get_adapter(settings)
    conn = get_db(settings.plugin_name)
    try:
        paid_lines = adapter.list_paid_orders(limit=inp.limit)
        enriched: list[dict[str, Any]] = []

        for line in paid_lines:
            local_item = (
                (get_item_by_sku(conn, line.sku) if line.sku else None)
                or get_item_by_ebay_item_id(conn, line.ebay_item_id)
            )
            bin_code = local_item["bin_code"] if local_item and local_item["bin_code"] else None
            resolved_sku = (local_item["sku"] if local_item else None) or line.sku
            enriched.append(
                {
                    "bin_code": bin_code or "UNASSIGNED",
                    "_sort_bin": bin_code,
                    "order_id": line.order_id,
                    "ebay_item_id": line.ebay_item_id,
                    "sku": resolved_sku,
                    "title": line.title,
                    "quantity": line.quantity,
                    "sold_price_cents": line.sold_price_cents,
                    "buyer_username": line.buyer_username,
                    "paid_at": line.paid_at,
                }
            )

        enriched.sort(key=lambda row: (_natural_bin_key(row["_sort_bin"]), row["order_id"]))
        pick_list = [{k: v for k, v in row.items() if k != "_sort_bin"} for row in enriched]

        md_lines = [
            f"### Warehouse Fulfillment Pick-List ({len(pick_list)} paid line items)",
            "| Bin | Qty | SKU | Title | Order ID | Buyer |",
            "| :--- | :---: | :--- | :--- | :--- | :--- |",
        ]
        for row in pick_list:
            md_lines.append(
                f"| `{row['bin_code']}` | {row['quantity']} | `{row['sku'] or '—'}` | "
                f"{row['title']} | `{row['order_id']}` | `{row['buyer_username']}` |"
            )

        return format_tool_response(
            {
                "ok": True,
                "tool": "ebay_lookup_bin",
                "count": len(pick_list),
                "pick_list": pick_list,
                "preview_card_md": "\n".join(md_lines),
            }
        )
    except ProviderNotSignedIn as exc:
        return format_tool_response(
            {
                "ok": False,
                "error_code": "AUTH_REQUIRED",
                "message": f"{NOT_CONNECTED_MESSAGE} ({exc})",
            }
        )
    except EbayApiError as exc:
        return format_tool_response({"ok": False, "error_code": "EBAY_API_ERROR", "message": str(exc)})
    finally:
        conn.close()
