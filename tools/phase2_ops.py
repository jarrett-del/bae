"""Phase 2 Pro reseller tools (gated via check_fn=is_pro_ops_enabled):
- ebay_triage_offers
- ebay_lookup_bin
"""

from __future__ import annotations

import re
from typing import Any

from hermes_cli.plugin_provider_requests import ProviderNotSignedIn
from pydantic import ValidationError

from ..adapter import EbayApiError, get_adapter
from ..config import get_settings
from ..db import (
    append_item_event,
    get_db,
    get_item_by_ebay_item_id,
    get_item_by_sku,
    get_or_create_seller_profile,
    stage_pending_action,
)
from ..models import (
    ActionType,
    BestOfferLifecycleStatus,
    EventActor,
    LookupBinInput,
    OfferDecisionAction,
    RespondOfferPayload,
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

        return format_tool_response(
            {
                "ok": True,
                "tool": "ebay_triage_offers",
                "actionable_offers": actionable_results,
                "awaiting_buyer_payment": awaiting_payment,
                "skipped_non_active": skipped_other,
                "preview_card_md": "\n".join(md_lines),
            }
        )
    except ProviderNotSignedIn as exc:
        return format_tool_response(
            {
                "ok": False,
                "error_code": "AUTH_REQUIRED",
                "message": f"Run `hermes auth add hermes-ebay-ops ebay` to connect your eBay account ({exc}).",
            }
        )
    except EbayApiError as exc:
        return format_tool_response({"ok": False, "error_code": "EBAY_API_ERROR", "message": str(exc)})
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
                "message": f"Run `hermes auth add hermes-ebay-ops ebay` to connect your eBay account ({exc}).",
            }
        )
    except EbayApiError as exc:
        return format_tool_response({"ok": False, "error_code": "EBAY_API_ERROR", "message": str(exc)})
    finally:
        conn.close()
