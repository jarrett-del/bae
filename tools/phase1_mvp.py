"""Phase 1 core tools (always active when hermes-ebay-ops is enabled):
- ebay_stage_listing
- ebay_audit_active_listing
- ebay_check_comps
- ebay_confirm_action
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from hermes_cli.plugin_provider_requests import ProviderNotSignedIn
from pydantic import ValidationError

from ..adapter import EbayApiError, get_adapter
from ..compliance import screen_listing_compliance
from ..config import NOT_CONNECTED_MESSAGE, get_settings
from ..csv_drafts import generate_seller_hub_draft_csv
from ..db import (
    ActionGateError,
    append_item_event,
    find_realized_sold_comps,
    get_db,
    get_item_by_ebay_item_id,
    get_item_by_sku,
    get_or_create_seller_profile,
    mark_pending_action_status,
    maybe_enable_pro_ops,
    stage_pending_action,
    update_seller_capabilities,
    upsert_item,
    verify_pending_action_for_execution,
)
from ..file_guard import local_read_block_error
from ..models import (
    ActionStatus,
    ActionType,
    AuditActiveListingInput,
    BootstrapPoliciesPayload,
    BulkPublishPayload,
    CheckCompsInput,
    ConditionEnum,
    ConfirmActionInput,
    EventActor,
    ExportDraftCsvInput,
    ItemStatus,
    RespondOfferPayload,
    ReviseListingPayload,
    SourceMode,
    StageListingInput,
    StagedListingPayload,
    mint_sku,
)
from . import (
    compute_dimensional_shipping,
    compute_economics,
    detect_boilerplate_and_inad_risks,
    extract_structured_notes,
    format_tool_response,
    format_usd,
    shipping_weight_sanity_note,
)

SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".bmp", ".tiff", ".tif"}

SCHEMA_STAGE_LISTING: dict[str, Any] = {
    "name": "ebay_stage_listing",
    "description": (
        "Stage a new eBay listing draft locally in SQLite without publishing it live. "
        "Progressive C2C/Pro etiquette: NEVER require SKU, COGS, floor price, or warehouse bin from casual sellers; "
        "only pass cogs_cents, bin_code, or floor_price_cents when the seller mentions them. "
        "Extract measurements and flaws strictly from seller_notes without inventing item specifics. "
        "Always present the returned preview_card_md and ask explicit human confirmation before calling ebay_confirm_action."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "media_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Local image file paths or existing HTTPS image URLs for the item.",
            },
            "seller_notes": {
                "type": "string",
                "description": "Raw voice/text notes describing the item, tag size, ruler measurements, and any flaws.",
            },
            "title_hint": {
                "type": "string",
                "description": "Optional concise listing title hint (max 80 chars, factual keywords, no marketing fluff).",
            },
            "price_cents": {
                "type": "integer",
                "description": "Optional list price in integer cents (e.g. 6500 for $65.00). Defaults to active comp median if omitted.",
            },
            "condition": {
                "type": "string",
                "description": "Optional condition enum (e.g. NEW, LIKE_NEW, USED_EXCELLENT, USED_GOOD, USED_ACCEPTABLE, FOR_PARTS_OR_NOT_WORKING).",
            },
            "cogs_cents": {
                "type": "integer",
                "description": "Optional Pro seller cost of goods sold in integer cents. Unlocks Pro margin mode automatically.",
            },
            "bin_code": {
                "type": "string",
                "description": "Optional Pro seller storage bin location code (e.g. 'A-02-04'). Unlocks Pro warehouse mode automatically.",
            },
            "floor_price_cents": {
                "type": "integer",
                "description": "Optional Pro minimum acceptable Best Offer price in integer cents.",
            },
            "package_weight_oz": {
                "type": "number",
                "description": "Optional packed shipping weight in ounces for carrier tier sanity checks.",
            },
            "package_dims": {
                "type": "object",
                "properties": {
                    "length_in": {"type": "number"},
                    "width_in": {"type": "number"},
                    "height_in": {"type": "number"},
                },
                "description": "Optional packed parcel dimensions in inches for July 2026 USPS 139 DIM-weight and >48 in oversize checks.",
            },
            "plg_ad_rate_bps": {
                "type": "integer",
                "description": "Optional Jan 2026 Promoted Listings General 30-day attribution ad rate in basis points (e.g. 500 for 5.0%).",
            },
        },
        "required": ["media_paths", "seller_notes"],
    },
}

SCHEMA_AUDIT_ACTIVE_LISTING: dict[str, Any] = {
    "name": "ebay_audit_active_listing",
    "description": (
        "Audit a live eBay listing (defaulting to the seller's most recent mobile Magical Listing) against "
        "required/recommended category aspects, missing ruler measurements, generic AI boilerplate that invites "
        "INAD returns, and active Browse API price comps. Stages a REVISE_LISTING approval token in SQLite; "
        "always show the QA scorecard and diff preview to the seller before calling ebay_confirm_action."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ebay_item_id": {
                "type": "string",
                "description": "Optional eBay Item ID to audit. Omit to audit the most recent active Magical Listing.",
            },
            "seller_notes": {
                "type": "string",
                "description": "Optional additional measurements or flaw disclosures provided by the seller during QA.",
            },
            "new_price_cents": {
                "type": "integer",
                "description": "Optional revised list price in integer cents.",
            },
            "cogs_cents": {
                "type": "integer",
                "description": "Optional Pro seller cost of goods sold in integer cents to record in local ledger.",
            },
            "bin_code": {
                "type": "string",
                "description": "Optional Pro warehouse bin code to assign to this active listing.",
            },
            "floor_price_cents": {
                "type": "integer",
                "description": "Optional minimum acceptable Best Offer floor price in integer cents.",
            },
        },
        "required": [],
    },
}

SCHEMA_CHECK_COMPS: dict[str, Any] = {
    "name": "ebay_check_comps",
    "description": (
        "Read-only active market comp check via eBay REST Browse API. Computes P25, median, and P75 active listing "
        "prices, typical shipping charges, and estimated net seller proceeds after eBay fees (plus net profit and "
        "margin % when cogs_cents is provided)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords to search active eBay listings (brand, model, size, material).",
            },
            "category_id": {
                "type": "string",
                "description": "Optional eBay leaf category ID to narrow comp results.",
            },
            "condition": {
                "type": "string",
                "description": "Optional item condition filter (e.g. NEW, USED_EXCELLENT, USED_GOOD).",
            },
            "cogs_cents": {
                "type": "integer",
                "description": "Optional cost of goods sold in integer cents to calculate net profit and margin %.",
            },
            "plg_ad_rate_bps": {
                "type": "integer",
                "description": "Optional Promoted Listings General ad rate in basis points (e.g. 500 for 5.0%).",
            },
            "buyer_tax_rate_bps": {
                "type": "integer",
                "description": "Optional estimated buyer sales-tax basis in basis points (e.g. 700 for ~7% US sales tax included in FVF).",
            },
            "image_b64": {
                "type": "string",
                "description": "Optional base64-encoded photo for Browse API search_by_image visual comps on vintage/unbranded items.",
            },
        },
        "required": ["query"],
    },
}

SCHEMA_EXPORT_DRAFT_CSV: dict[str, Any] = {
    "name": "ebay_export_draft_csv",
    "description": (
        "Dual-path Seller Hub Reports Draft CSV generator (Windows CRLF `\\r\\n`, `Action=Draft`, valid 75 days). "
        "Solves the eBay REST Drafts limitation for casual sellers without Business Policies while sanitizing "
        "description line breaks so Seller Hub never fails with `BAF.Error.5`."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "skus": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of local SKUs to export. Omit to export all STAGED drafts.",
            },
            "status_filter": {
                "type": "string",
                "enum": ["STAGED", "ALL"],
                "description": "Export STAGED drafts only (default) or ALL local inventory.",
            },
            "location_label": {
                "type": "string",
                "description": "Item location string written into the `*Location` column (default 'United States').",
            },
        },
        "required": [],
    },
}

SCHEMA_CONFIRM_ACTION: dict[str, Any] = {
    "name": "ebay_confirm_action",
    "description": (
        "Execute or cancel a previously staged eBay write action using its single-use cryptographic action_token. "
        "decision='APPROVE' always opens Hermes's own approval prompt, and the eBay write runs only if the person "
        "approves it there; it is blocked when nobody is present to answer (cron and other unattended runs). "
        "Show the preview card first and call APPROVE only after the person has asked for it in chat. "
        "decision='CANCEL' voids the token without a prompt."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action_token": {
                "type": "string",
                "description": "Single-use action_token returned by ebay_stage_listing, ebay_audit_active_listing, or ebay_triage_offers.",
            },
            "decision": {
                "type": "string",
                "enum": ["APPROVE", "CANCEL"],
                "description": "APPROVE to execute the verified eBay write, or CANCEL to void the token.",
            },
        },
        "required": ["action_token", "decision"],
    },
}


def _validate_local_media_paths(media_paths: list[str]) -> tuple[list[str], list[str]]:
    """Separate local image paths from HTTPS URLs and vet local files before staging.

    A local path becomes a public eBay listing photo, so Hermes's read denylist is applied first,
    before the plugin stats or resolves the file.
    """
    local_files: list[str] = []
    remote_urls: list[str] = []
    for raw in media_paths:
        cleaned = raw.strip()
        if not cleaned:
            continue
        if cleaned.startswith(("https://", "http://")):
            remote_urls.append(cleaned)
            continue
        blocked = local_read_block_error(cleaned)
        if blocked:
            raise ValueError(blocked)
        path = Path(cleaned)
        if not path.exists() or not path.is_file():
            raise ValueError(f"Media file does not exist: {cleaned}")
        if path.stat().st_size == 0:
            raise ValueError(f"Media file is empty: {cleaned}")
        if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported image extension {path.suffix!r} for {cleaned}")
        local_files.append(str(path.resolve()))
    return local_files, remote_urls


def handle_stage_listing(
    params: dict[str, Any],
    *,
    session_id: str = "",
    platform: str = "",
    **kwargs: Any,
) -> str:
    try:
        inp = StageListingInput.model_validate(params or {})
        local_media, remote_urls = _validate_local_media_paths(inp.media_paths)
    except (ValidationError, ValueError) as exc:
        return format_tool_response({"ok": False, "error_code": "INVALID_INPUT", "message": str(exc)})

    settings = get_settings()
    adapter = get_adapter(settings)
    conn = get_db(settings.plugin_name)
    try:
        extracted = extract_structured_notes(inp.seller_notes, inp.title_hint)
        sku = inp.sku or mint_sku()
        condition = inp.condition or ConditionEnum.USED_GOOD

        category_id = adapter.suggest_category(extracted.title, settings.marketplace_id)
        aspect_specs = adapter.get_category_aspects(category_id, settings.marketplace_id)

        profile = get_or_create_seller_profile(conn)
        capabilities = adapter.check_policy_status(settings.marketplace_id)
        profile = update_seller_capabilities(conn, capabilities)

        pro_active = maybe_enable_pro_ops(
            conn,
            cogs_cents=inp.cogs_cents,
            bin_code=inp.bin_code,
            floor_price_cents=inp.floor_price_cents,
        )
        if settings.mode == "pro":
            pro_active = True
        elif settings.mode == "lite":
            pro_active = False

        if inp.price_cents is not None:
            list_price_cents = inp.price_cents
            shipping_est_cents = 850
        else:
            comps = adapter.search_comps(extracted.title, category_id, condition.value)
            list_price_cents = comps.median_cents if comps.median_cents > 0 else 2500
            shipping_est_cents = comps.typical_shipping_cents if comps.typical_shipping_cents > 0 else 850

        route_mode: Literal["INVENTORY_REST", "TRADING_XML_FALLBACK"] = (
            "INVENTORY_REST" if capabilities.policies_opted_in else "TRADING_XML_FALLBACK"
        )

        compliance = screen_listing_compliance(
            title=extracted.title,
            description=extracted.clean_description,
            category_id=category_id,
            condition=condition,
            list_price_cents=list_price_cents,
            aspects=extracted.aspects,
            media_urls_or_paths=remote_urls or local_media,
            return_policy_id=capabilities.return_policy_id,
            sku=sku,
            conn=conn,
        )
        dim_check = compute_dimensional_shipping(
            inp.package_weight_oz,
            inp.package_dims,
            list_price_cents=list_price_cents,
            category_id=category_id,
            fulfillment_policy_id=capabilities.fulfillment_policy_id,
        )

        staged_payload = StagedListingPayload(
            sku=sku,
            title=extracted.title,
            description=extracted.clean_description,
            category_id=category_id,
            condition=condition,
            list_price_cents=list_price_cents,
            shipping_est_cents=shipping_est_cents,
            package_weight_oz=inp.package_weight_oz,
            package_dims=inp.package_dims,
            aspects=compliance.sanitized_aspects,
            flaws_disclosure=extracted.flaws_disclosure,
            media_paths=local_media,
            media_urls=remote_urls,
            marketplace_id=settings.marketplace_id,
            route_mode=route_mode,
            fulfillment_policy_id=capabilities.fulfillment_policy_id,
            payment_policy_id=capabilities.payment_policy_id,
            return_policy_id=capabilities.return_policy_id,
            merchant_location_key=capabilities.merchant_location_key,
            cogs_cents=inp.cogs_cents,
            bin_code=inp.bin_code,
            floor_price_cents=inp.floor_price_cents,
        )

        missing_required = [
            spec.name for spec in aspect_specs if spec.required and spec.name not in staged_payload.aspects
        ]
        missing_recommended = [
            spec.name
            for spec in aspect_specs
            if spec.usage == "RECOMMENDED" and spec.name not in staged_payload.aspects
        ]

        econ = compute_economics(
            list_price_cents=list_price_cents,
            shipping_est_cents=shipping_est_cents,
            estimated_fee_bps=int(profile["estimated_fee_bps"]),
            cogs_cents=inp.cogs_cents if pro_active else None,
            plg_ad_rate_bps=inp.plg_ad_rate_bps,
        )
        weight_note = dim_check.summary_note

        card_lines = [
            f"### Staged eBay Listing Preview (`{sku}`)",
            f"- **Title**: {staged_payload.title}",
            f"- **Category ID**: `{category_id}` (`{compliance.playbook}`) | **Condition**: `{condition.value}` (`{condition.browse_condition_id(category_id)}`)",
            f"- **Publish Route**: `{route_mode}` ({'Business Policies active' if capabilities.policies_opted_in else 'Casual C2C inline shipping/returns fallback'})",
            f"- **List Price**: {format_usd(list_price_cents)} (+ {format_usd(shipping_est_cents)} est. buyer shipping)",
            f"- **Estimated eBay Fees**: -{format_usd(econ.estimated_fees_cents)}"
            + (f" *(includes {format_usd(econ.plg_ad_fee_cents)} PLG {inp.plg_ad_rate_bps / 100:.1f}% ad fee)*" if econ.plg_ad_fee_cents else ""),
            f"- **Estimated Net Payout**: **{format_usd(econ.estimated_net_payout_cents)}**",
        ]
        if pro_active:
            if econ.cogs_cents is not None and econ.net_profit_cents is not None and econ.margin_pct is not None:
                card_lines.append(
                    f"- **Pro Unit Economics**: COGS {format_usd(econ.cogs_cents)} | "
                    f"**Net Profit**: **{format_usd(econ.net_profit_cents)}** (**{econ.margin_pct:.1f}% Margin**)"
                )
            if inp.bin_code:
                card_lines.append(f"- **Warehouse Bin**: `{inp.bin_code}`")
            if inp.floor_price_cents:
                card_lines.append(f"- **Best Offer Floor**: {format_usd(inp.floor_price_cents)}")

        card_lines.append(
            f"- **Populated Aspects**: {', '.join(f'{k}={v}' for k, v in staged_payload.aspects.items()) or 'None'}"
        )
        card_lines.append(
            f"- **Missing Required Aspects**: {', '.join(missing_required) if missing_required else 'None (all required populated)'}"
        )
        if missing_recommended:
            card_lines.append(f"- **Recommended Aspects to Add**: {', '.join(missing_recommended)}")
        if staged_payload.flaws_disclosure:
            card_lines.append(f"- **Disclosed Flaws**: {staged_payload.flaws_disclosure}")
        card_lines.append(f"- **Shipping / 2026 139-DIM Check**: {weight_note}")
        for warn in [*compliance.warnings, *dim_check.warnings]:
            card_lines.append(f"- **2026 Compliance / Margin Guard**: {warn}")

        upsert_item(
            conn,
            sku=sku,
            source_mode=SourceMode.AGENT_CAPTURE,
            status=ItemStatus.STAGED,
            title=staged_payload.title,
            category_id=category_id,
            condition_enum=condition,
            list_price_cents=list_price_cents,
            shipping_est_cents=shipping_est_cents,
            package_weight_oz=inp.package_weight_oz,
            package_dims=inp.package_dims.model_dump(mode="json") if inp.package_dims else None,
            aspects=staged_payload.aspects,
            flaws_disclosure=staged_payload.flaws_disclosure,
            media_urls=remote_urls or local_media,
            cogs_cents=inp.cogs_cents,
            bin_code=inp.bin_code,
            floor_price_cents=inp.floor_price_cents,
        )

        # Stage cryptographic two-step approval token
        temp_preview = "\n".join(card_lines)
        pending = stage_pending_action(
            conn,
            action_type=ActionType.PUBLISH_LISTING,
            sku=sku,
            payload=staged_payload.model_dump(mode="json"),
            preview_card_md=temp_preview,
        )
        final_card = (
            temp_preview
            + f"\n- **Approval Token**: `{pending.action_token}` *(expires {pending.expires_at})*"
        )
        conn.execute(
            "UPDATE pending_actions SET preview_card_md = ? WHERE action_token = ?",
            (final_card, pending.action_token),
        )
        conn.commit()

        append_item_event(
            conn,
            sku=sku,
            event_type="LISTING_STAGED",
            actor=EventActor.AGENT_STAGED,
            detail={
                "action_token": pending.action_token,
                "list_price_cents": list_price_cents,
                "route_mode": route_mode,
                "playbook": compliance.playbook,
                "session_id": session_id,
                "platform": platform,
            },
        )

        return format_tool_response(
            {
                "ok": True,
                "tool": "ebay_stage_listing",
                "mode": "pro" if pro_active else "lite",
                "sku": sku,
                "playbook": compliance.playbook,
                "action_token": pending.action_token,
                "expires_at": pending.expires_at,
                "missing_required_aspects": missing_required,
                "missing_recommended_aspects": missing_recommended,
                "compliance_warnings": [*compliance.warnings, *dim_check.warnings],
                "dimensional_shipping": {
                    "cubic_inches": dim_check.cubic_inches,
                    "actual_lbs": dim_check.actual_lbs,
                    "dim_lbs_2026": dim_check.dim_lbs_2026,
                    "billable_lbs": dim_check.billable_lbs,
                    "dim_triggered": dim_check.dim_triggered,
                    "oversize_triggered": dim_check.oversize_triggered,
                    "recommended_box_code": dim_check.recommended_box_code,
                },
                "economics": {
                    "list_price_cents": econ.list_price_cents,
                    "estimated_fees_cents": econ.estimated_fees_cents,
                    "plg_ad_fee_cents": econ.plg_ad_fee_cents,
                    "very_high_inad_risk_cents": econ.very_high_inad_risk_cents,
                    "estimated_net_payout_cents": econ.estimated_net_payout_cents,
                    "cogs_cents": econ.cogs_cents,
                    "net_profit_cents": econ.net_profit_cents,
                    "margin_pct": econ.margin_pct,
                },
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


def handle_audit_active_listing(
    params: dict[str, Any],
    *,
    session_id: str = "",
    platform: str = "",
    **kwargs: Any,
) -> str:
    try:
        inp = AuditActiveListingInput.model_validate(params or {})
    except ValidationError as exc:
        return format_tool_response({"ok": False, "error_code": "INVALID_INPUT", "message": str(exc)})

    settings = get_settings()
    adapter = get_adapter(settings)
    conn = get_db(settings.plugin_name)
    try:
        active_listings = adapter.fetch_recent_active_listings(limit=10)
        if not active_listings:
            return format_tool_response(
                {"ok": False, "error_code": "NO_ACTIVE_LISTINGS", "message": "No active eBay listings found to audit."}
            )

        target = None
        if inp.ebay_item_id:
            for item in active_listings:
                if item.ebay_item_id == inp.ebay_item_id:
                    target = item
                    break
            if target is None:
                return format_tool_response(
                    {
                        "ok": False,
                        "error_code": "LISTING_NOT_FOUND",
                        "message": f"Active listing {inp.ebay_item_id!r} not found in recent active listings.",
                    }
                )
        else:
            magical = [item for item in active_listings if item.created_via_magical_listing]
            target = magical[0] if magical else active_listings[0]

        aspect_specs = adapter.get_category_aspects(target.category_id, settings.marketplace_id)
        comps = adapter.search_comps(target.title, target.category_id, target.condition.value)

        merged_aspects = dict(target.aspects)
        extra_notes = None
        if inp.seller_notes:
            extra_notes = extract_structured_notes(inp.seller_notes, target.title)
            for k, v in extra_notes.aspects.items():
                merged_aspects.setdefault(k, v)

        boilerplate_hits, inad_warnings, cleaned_desc = detect_boilerplate_and_inad_risks(
            description=target.description,
            aspects=target.aspects,
            category_id=target.category_id,
        )
        compliance = screen_listing_compliance(
            title=target.title,
            description=cleaned_desc,
            category_id=target.category_id,
            condition=target.condition,
            list_price_cents=target.list_price_cents,
            aspects=merged_aspects,
            media_urls_or_paths=target.media_urls,
        )
        merged_aspects = {
            k: ([x.strip() for x in v.split(",") if x.strip()] if isinstance(v, str) else list(v))
            for k, v in compliance.sanitized_aspects.items()
        }
        for cw in compliance.warnings:
            if cw not in inad_warnings:
                inad_warnings.append(cw)
        if extra_notes:
            if extra_notes.measurements:
                cleaned_desc = f"{cleaned_desc}\nMeasurements: {', '.join(extra_notes.measurements)}."
            if extra_notes.flaws_disclosure:
                cleaned_desc = f"{cleaned_desc}\nCondition Disclosure: {extra_notes.flaws_disclosure}."

        missing_required = [s.name for s in aspect_specs if s.required and s.name not in merged_aspects]
        missing_recommended = [
            s.name for s in aspect_specs if s.usage == "RECOMMENDED" and s.name not in merged_aspects
        ]

        price_warning: str | None = None
        if comps.sample_count > 0 and comps.p75_cents > 0:
            if target.list_price_cents > int(comps.p75_cents * 1.15):
                price_warning = (
                    f"Listed at {format_usd(target.list_price_cents)} vs comp P75 {format_usd(comps.p75_cents)} "
                    f"(median {format_usd(comps.median_cents)}) — overpriced risk."
                )
            elif target.list_price_cents < int(comps.p25_cents * 0.85):
                price_warning = (
                    f"Listed at {format_usd(target.list_price_cents)} vs comp P25 {format_usd(comps.p25_cents)} — "
                    f"potentially leaving margin on the table."
                )

        # Compute QA score (0-100)
        score = 100
        score -= 15 * len(missing_required)
        score -= 8 * len(missing_recommended)
        score -= 20 if boilerplate_hits else 0
        score -= 20 if any("measurements" in w.lower() for w in inad_warnings) else 0
        score -= 10 if price_warning else 0
        score = max(0, score)

        existing_local = get_item_by_ebay_item_id(conn, target.ebay_item_id)
        sku = (
            (existing_local["sku"] if existing_local else None)
            or target.sku
            or mint_sku()
        )

        pro_active = maybe_enable_pro_ops(
            conn,
            cogs_cents=inp.cogs_cents,
            bin_code=inp.bin_code,
            floor_price_cents=inp.floor_price_cents,
        )
        if settings.mode == "pro":
            pro_active = True
        elif settings.mode == "lite":
            pro_active = False

        proposed_price_cents = inp.new_price_cents or target.list_price_cents
        upsert_item(
            conn,
            sku=sku,
            ebay_item_id=target.ebay_item_id,
            offer_id=target.offer_id,
            source_mode=SourceMode.MAGICAL_AUDIT,
            status=ItemStatus.ACTIVE,
            title=target.title,
            category_id=target.category_id,
            condition_enum=target.condition,
            list_price_cents=target.list_price_cents,
            shipping_est_cents=target.shipping_est_cents,
            aspects=target.aspects,
            flaws_disclosure=extra_notes.flaws_disclosure if extra_notes else None,
            media_urls=target.media_urls,
            cogs_cents=inp.cogs_cents,
            bin_code=inp.bin_code,
            floor_price_cents=inp.floor_price_cents,
            listed_at=target.listed_at,
        )

        changes: dict[str, Any] = {
            "title": target.title,
            "description": cleaned_desc,
            "aspects": merged_aspects,
            "list_price_cents": proposed_price_cents,
            "condition": target.condition.value,
            "media_urls": target.media_urls,
        }
        if target.offer_id:
            changes["offer_id"] = target.offer_id
            changes["route_mode"] = "INVENTORY_REST"

        revise_payload = ReviseListingPayload(
            sku=sku,
            ebay_item_id=target.ebay_item_id,
            offer_id=target.offer_id,
            changes=changes,
        )

        diff_lines = [
            f"### Active Listing QA Scorecard — `{target.ebay_item_id}` (Score: **{score}/100**)",
            f"- **Title**: {target.title}",
            f"- **Local SKU**: `{sku}` | **Magical Listing Origin**: `{'Yes' if target.created_via_magical_listing else 'No'}`",
            f"- **Active Price vs Comps**: {format_usd(target.list_price_cents)} "
            f"(P25 {format_usd(comps.p25_cents)} | Median {format_usd(comps.median_cents)} | P75 {format_usd(comps.p75_cents)})",
        ]
        if price_warning:
            diff_lines.append(f"- **Price Alert**: {price_warning}")
        for warn in inad_warnings:
            diff_lines.append(f"- **INAD Risk Flag**: {warn}")
        if missing_required:
            diff_lines.append(f"- **Missing Required Specifics**: {', '.join(missing_required)}")
        if missing_recommended:
            diff_lines.append(f"- **Missing Recommended Specifics**: {', '.join(missing_recommended)}")
        diff_lines.extend(
            [
                "",
                "#### Proposed Revision Diff",
                f"- **Description (Before)**: `{target.description}`",
                f"- **Description (After)**: `{cleaned_desc}`",
                f"- **Revised Price**: {format_usd(proposed_price_cents)}",
            ]
        )

        base_card = "\n".join(diff_lines)
        pending = stage_pending_action(
            conn,
            action_type=ActionType.REVISE_LISTING,
            sku=sku,
            payload=revise_payload.model_dump(mode="json"),
            preview_card_md=base_card,
        )
        final_card = base_card + f"\n- **Approval Token**: `{pending.action_token}` *(expires {pending.expires_at})*"
        conn.execute(
            "UPDATE pending_actions SET preview_card_md = ? WHERE action_token = ?",
            (final_card, pending.action_token),
        )
        conn.commit()

        append_item_event(
            conn,
            sku=sku,
            event_type="AUDIT_STAGED",
            actor=EventActor.AGENT_STAGED,
            detail={
                "ebay_item_id": target.ebay_item_id,
                "qa_score": score,
                "boilerplate_hits": boilerplate_hits,
                "action_token": pending.action_token,
                "session_id": session_id,
                "platform": platform,
            },
        )

        return format_tool_response(
            {
                "ok": True,
                "tool": "ebay_audit_active_listing",
                "mode": "pro" if pro_active else "lite",
                "sku": sku,
                "ebay_item_id": target.ebay_item_id,
                "qa_score": score,
                "inad_warnings": inad_warnings,
                "boilerplate_hits": boilerplate_hits,
                "missing_required_aspects": missing_required,
                "missing_recommended_aspects": missing_recommended,
                "price_warning": price_warning,
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


def handle_check_comps(
    params: dict[str, Any],
    *,
    session_id: str = "",
    platform: str = "",
    **kwargs: Any,
) -> str:
    try:
        inp = CheckCompsInput.model_validate(params or {})
    except ValidationError as exc:
        return format_tool_response({"ok": False, "error_code": "INVALID_INPUT", "message": str(exc)})

    settings = get_settings()
    adapter = get_adapter(settings)
    conn = get_db(settings.plugin_name)
    try:
        profile = get_or_create_seller_profile(conn)
        fee_bps = int(profile["estimated_fee_bps"])
        cond_val = inp.condition.value if inp.condition else None
        if inp.image_b64:
            comps = adapter.search_comps_by_image(inp.image_b64, inp.category_id, cond_val)
        else:
            comps = adapter.search_comps(inp.query, inp.category_id, cond_val)

        realized_local = find_realized_sold_comps(conn, inp.query, inp.category_id, limit=5)
        econ_median = compute_economics(
            list_price_cents=max(1, comps.median_cents) if comps.sample_count > 0 else 1,
            shipping_est_cents=comps.typical_shipping_cents,
            estimated_fee_bps=fee_bps,
            cogs_cents=inp.cogs_cents,
            plg_ad_rate_bps=inp.plg_ad_rate_bps,
            buyer_tax_rate_bps=inp.buyer_tax_rate_bps,
        )

        md_lines = [
            f"### Active eBay Comps — `{inp.query}` ({comps.sample_count} samples"
            + (" · Visual Image Search" if inp.image_b64 else "")
            + ")",
            f"- **P25**: {format_usd(comps.p25_cents)} | **Median**: **{format_usd(comps.median_cents)}** | **P75**: {format_usd(comps.p75_cents)}",
            f"- **Typical Buyer Shipping**: {format_usd(comps.typical_shipping_cents)}",
        ]
        if realized_local:
            avg_sold = int(round(sum(int(r["list_price_cents"]) for r in realized_local) / len(realized_local)))
            md_lines.append(
                f"- **Realized Store Sold History**: {len(realized_local)} local sold match(es), avg **{format_usd(avg_sold)}**"
            )
        if comps.sample_count > 0:
            md_lines.append(
                f"- **Est. Net Seller Proceeds at Median**: **{format_usd(econ_median.estimated_net_payout_cents)}** "
                f"(after {format_usd(econ_median.estimated_fees_cents)} fees)"
            )
            if inp.cogs_cents is not None and econ_median.net_profit_cents is not None and econ_median.margin_pct is not None:
                md_lines.append(
                    f"- **Est. Net Profit at Median**: **{format_usd(econ_median.net_profit_cents)}** "
                    f"(**{econ_median.margin_pct:.1f}% Margin** on {format_usd(inp.cogs_cents)} COGS)"
                )

        return format_tool_response(
            {
                "ok": True,
                "tool": "ebay_check_comps",
                "query": inp.query,
                "search_mode": "image" if inp.image_b64 else "keyword",
                "sample_count": comps.sample_count,
                "p25_cents": comps.p25_cents,
                "median_cents": comps.median_cents,
                "p75_cents": comps.p75_cents,
                "typical_shipping_cents": comps.typical_shipping_cents,
                "median_net_proceeds_cents": econ_median.estimated_net_payout_cents if comps.sample_count > 0 else 0,
                "plg_ad_fee_cents": econ_median.plg_ad_fee_cents if comps.sample_count > 0 else 0,
                "very_high_inad_risk_cents": econ_median.very_high_inad_risk_cents if comps.sample_count > 0 else 0,
                "median_net_profit_cents": econ_median.net_profit_cents if comps.sample_count > 0 else None,
                "median_margin_pct": econ_median.margin_pct if comps.sample_count > 0 else None,
                "realized_sold_count": len(realized_local),
                "realized_sold_samples": realized_local,
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


def handle_confirm_action(
    params: dict[str, Any],
    *,
    session_id: str = "",
    platform: str = "",
    **kwargs: Any,
) -> str:
    try:
        inp = ConfirmActionInput.model_validate(params or {})
    except ValidationError as exc:
        return format_tool_response({"ok": False, "error_code": "INVALID_INPUT", "message": str(exc)})

    settings = get_settings()
    conn = get_db(settings.plugin_name)
    try:
        try:
            record = verify_pending_action_for_execution(conn, inp.action_token)
        except ActionGateError as gate_err:
            return format_tool_response(
                {
                    "ok": False,
                    "error_code": gate_err.code,
                    "action_token": inp.action_token,
                    "message": gate_err.message,
                }
            )

        if inp.decision == "CANCEL":
            mark_pending_action_status(conn, record.action_token, ActionStatus.CANCELLED)
            if record.sku and get_item_by_sku(conn, record.sku) is not None:
                append_item_event(
                    conn,
                    sku=record.sku,
                    event_type="ACTION_CANCELLED",
                    actor=EventActor.USER,
                    detail={
                        "action_token": record.action_token,
                        "action_type": record.action_type.value,
                        "session_id": session_id,
                    },
                )
            return format_tool_response(
                {
                    "ok": True,
                    "tool": "ebay_confirm_action",
                    "action_token": record.action_token,
                    "status": ActionStatus.CANCELLED.value,
                    "preview_card_md": f"Cancelled pending `{record.action_type.value}` action (`{record.action_token}`). Zero eBay changes were made.",
                }
            )

        # Decision == APPROVE: execute verified EbayAdapter write
        adapter = get_adapter(settings)
        try:
            if record.action_type == ActionType.PUBLISH_LISTING:
                pub_payload = StagedListingPayload.model_validate(record.payload)
                uploaded_urls = list(pub_payload.media_urls)
                if pub_payload.media_paths:
                    uploaded_urls.extend(adapter.upload_media(pub_payload.media_paths))
                final_pub = pub_payload.model_copy(update={"media_urls": uploaded_urls, "media_paths": []})
                receipt = adapter.publish_listing(final_pub)

                mark_pending_action_status(conn, record.action_token, ActionStatus.EXECUTED)
                upsert_item(
                    conn,
                    sku=final_pub.sku,
                    ebay_item_id=receipt.ebay_item_id,
                    offer_id=receipt.offer_id,
                    source_mode=SourceMode.AGENT_CAPTURE,
                    status=ItemStatus.ACTIVE,
                    title=final_pub.title,
                    category_id=final_pub.category_id,
                    condition_enum=final_pub.condition,
                    list_price_cents=final_pub.list_price_cents,
                    shipping_est_cents=final_pub.shipping_est_cents,
                    package_weight_oz=final_pub.package_weight_oz,
                    aspects=final_pub.aspects,
                    flaws_disclosure=final_pub.flaws_disclosure,
                    media_urls=uploaded_urls,
                    cogs_cents=final_pub.cogs_cents,
                    bin_code=final_pub.bin_code,
                    floor_price_cents=final_pub.floor_price_cents,
                    listed_at=receipt.timestamp_utc,
                )
                append_item_event(
                    conn,
                    sku=final_pub.sku,
                    event_type="LISTING_PUBLISHED",
                    actor=EventActor.USER,
                    detail={
                        "action_token": record.action_token,
                        "ebay_item_id": receipt.ebay_item_id,
                        "offer_id": receipt.offer_id,
                        "route_used": receipt.route_used,
                        "session_id": session_id,
                        "platform": platform,
                    },
                )
                return format_tool_response(
                    {
                        "ok": True,
                        "tool": "ebay_confirm_action",
                        "status": ActionStatus.EXECUTED.value,
                        "action_type": record.action_type.value,
                        "sku": final_pub.sku,
                        "ebay_item_id": receipt.ebay_item_id,
                        "offer_id": receipt.offer_id,
                        "route_used": receipt.route_used,
                        "preview_card_md": (
                            f"Published `{final_pub.title}` (`{final_pub.sku}`) live on eBay — "
                            f"Item ID `{receipt.ebay_item_id}` via `{receipt.route_used}`."
                        ),
                    }
                )

            if record.action_type == ActionType.REVISE_LISTING:
                rev_payload = ReviseListingPayload.model_validate(record.payload)
                receipt = adapter.revise_listing(
                    rev_payload.sku,
                    rev_payload.ebay_item_id,
                    rev_payload.changes,
                )
                mark_pending_action_status(conn, record.action_token, ActionStatus.EXECUTED)

                existing_item = get_item_by_sku(conn, rev_payload.sku)
                if existing_item is not None:
                    upsert_item(
                        conn,
                        sku=rev_payload.sku,
                        ebay_item_id=rev_payload.ebay_item_id,
                        offer_id=receipt.offer_id or existing_item.get("offer_id"),
                        source_mode=existing_item["source_mode"],
                        status=ItemStatus.ACTIVE,
                        title=str(rev_payload.changes.get("title") or existing_item["title"]),
                        category_id=str(existing_item["category_id"]),
                        condition_enum=str(rev_payload.changes.get("condition") or existing_item["condition_enum"]),
                        list_price_cents=int(rev_payload.changes.get("list_price_cents") or existing_item["list_price_cents"]),
                        shipping_est_cents=int(existing_item["shipping_est_cents"]),
                        aspects=rev_payload.changes.get("aspects"),
                        media_urls=rev_payload.changes.get("media_urls"),
                    )
                    append_item_event(
                        conn,
                        sku=rev_payload.sku,
                        event_type="LISTING_REVISED",
                        actor=EventActor.USER,
                        detail={
                            "action_token": record.action_token,
                            "ebay_item_id": rev_payload.ebay_item_id,
                            "route_used": receipt.route_used,
                            "session_id": session_id,
                        },
                    )
                return format_tool_response(
                    {
                        "ok": True,
                        "tool": "ebay_confirm_action",
                        "status": ActionStatus.EXECUTED.value,
                        "action_type": record.action_type.value,
                        "sku": rev_payload.sku,
                        "ebay_item_id": rev_payload.ebay_item_id,
                        "route_used": receipt.route_used,
                        "preview_card_md": f"Revised active eBay listing `{rev_payload.ebay_item_id}` (`{rev_payload.sku}`).",
                    }
                )

            if record.action_type == ActionType.BOOTSTRAP_POLICIES:
                boot_payload = BootstrapPoliciesPayload.model_validate(record.payload)
                caps = adapter.bootstrap_policies(boot_payload.marketplace_id)
                update_seller_capabilities(conn, caps)
                mark_pending_action_status(conn, record.action_token, ActionStatus.EXECUTED)
                return format_tool_response(
                    {
                        "ok": True,
                        "tool": "ebay_confirm_action",
                        "status": ActionStatus.EXECUTED.value,
                        "action_type": record.action_type.value,
                        "policies_opted_in": caps.policies_opted_in,
                        "preview_card_md": f"Opted into eBay Business Policies for `{caps.marketplace_id}`.",
                    }
                )

            if record.action_type == ActionType.RESPOND_OFFER:
                offer_payload = RespondOfferPayload.model_validate(record.payload)
                offer_receipt = adapter.respond_to_offer(offer_payload)
                mark_pending_action_status(conn, record.action_token, ActionStatus.EXECUTED)
                if offer_payload.sku and get_item_by_sku(conn, offer_payload.sku) is not None:
                    append_item_event(
                        conn,
                        sku=offer_payload.sku,
                        event_type="OFFER_RESPONDED",
                        actor=EventActor.USER,
                        detail={
                            "action_token": record.action_token,
                            "best_offer_id": offer_payload.best_offer_id,
                            "decision_action": offer_payload.action.value,
                            "resulting_status": offer_receipt.resulting_status.value,
                            "counter_price_cents": offer_payload.counter_price_cents,
                            "session_id": session_id,
                        },
                    )
                return format_tool_response(
                    {
                        "ok": True,
                        "tool": "ebay_confirm_action",
                        "status": ActionStatus.EXECUTED.value,
                        "action_type": record.action_type.value,
                        "best_offer_id": offer_receipt.best_offer_id,
                        "resulting_status": offer_receipt.resulting_status.value,
                        "preview_card_md": (
                            f"Executed `{offer_payload.action.value}` on Best Offer `{offer_receipt.best_offer_id}` "
                            f"(eBay status: `{offer_receipt.resulting_status.value}`)."
                        ),
                    }
                )

            if record.action_type == ActionType.BULK_PUBLISH:
                bulk_payload = BulkPublishPayload.model_validate(record.payload)
                ready_items: list[StagedListingPayload] = []
                for p in bulk_payload.items:
                    uploaded = list(p.media_urls)
                    if p.media_paths:
                        uploaded.extend(adapter.upload_media(p.media_paths))
                    ready_items.append(p.model_copy(update={"media_urls": uploaded, "media_paths": []}))

                receipts = adapter.bulk_publish_listings(ready_items)
                mark_pending_action_status(conn, record.action_token, ActionStatus.EXECUTED)

                receipt_by_sku = {r.sku: r for r in receipts}
                for item_pub in ready_items:
                    rcp = receipt_by_sku.get(item_pub.sku)
                    if rcp is None:
                        continue
                    upsert_item(
                        conn,
                        sku=item_pub.sku,
                        ebay_item_id=rcp.ebay_item_id,
                        offer_id=rcp.offer_id,
                        source_mode=SourceMode.AGENT_CAPTURE,
                        status=ItemStatus.ACTIVE,
                        title=item_pub.title,
                        category_id=item_pub.category_id,
                        condition_enum=item_pub.condition,
                        list_price_cents=item_pub.list_price_cents,
                        shipping_est_cents=item_pub.shipping_est_cents,
                        package_weight_oz=item_pub.package_weight_oz,
                        package_dims=item_pub.package_dims.model_dump(mode="json") if item_pub.package_dims else None,
                        aspects=item_pub.aspects,
                        flaws_disclosure=item_pub.flaws_disclosure,
                        media_urls=item_pub.media_urls,
                        cogs_cents=item_pub.cogs_cents,
                        bin_code=item_pub.bin_code,
                        floor_price_cents=item_pub.floor_price_cents,
                        listed_at=rcp.timestamp_utc,
                    )
                    append_item_event(
                        conn,
                        sku=item_pub.sku,
                        event_type="LISTING_PUBLISHED",
                        actor=EventActor.USER,
                        detail={
                            "action_token": record.action_token,
                            "ebay_item_id": rcp.ebay_item_id,
                            "offer_id": rcp.offer_id,
                            "route_used": rcp.route_used,
                            "bulk_batch": True,
                            "session_id": session_id,
                        },
                    )
                return format_tool_response(
                    {
                        "ok": True,
                        "tool": "ebay_confirm_action",
                        "status": ActionStatus.EXECUTED.value,
                        "action_type": record.action_type.value,
                        "published_count": len(receipts),
                        "receipts": [r.model_dump(mode="json") for r in receipts],
                        "preview_card_md": (
                            f"Bulk-published **{len(receipts)}** listings (`<=25 SKUs/call`) live on eBay "
                            f"via `{receipts[0].route_used if receipts else 'INVENTORY_REST'}`."
                        ),
                    }
                )

            mark_pending_action_status(conn, record.action_token, ActionStatus.FAILED)
            return format_tool_response(
                {
                    "ok": False,
                    "error_code": "UNSUPPORTED_ACTION_TYPE",
                    "message": f"Unsupported action_type: {record.action_type}",
                }
            )
        except (ValidationError, ProviderNotSignedIn, EbayApiError) as exc:
            mark_pending_action_status(conn, record.action_token, ActionStatus.FAILED)
            return format_tool_response(
                {
                    "ok": False,
                    "error_code": "EXECUTION_FAILED",
                    "action_token": record.action_token,
                    "message": str(exc),
                }
            )
    finally:
        conn.close()


def handle_export_draft_csv(
    params: dict[str, Any],
    *,
    session_id: str = "",
    platform: str = "",
    **kwargs: Any,
) -> str:
    """Export staged or selected inventory rows as a Windows CRLF Seller Hub Reports Draft CSV."""
    try:
        inp = ExportDraftCsvInput.model_validate(params or {})
    except ValidationError as exc:
        return format_tool_response({"ok": False, "error_code": "INVALID_INPUT", "message": str(exc)})

    settings = get_settings()
    conn = get_db(settings.plugin_name)
    try:
        if inp.skus:
            placeholders = ",".join("?" for _ in inp.skus)
            rows = [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM items WHERE sku IN ({placeholders}) ORDER BY updated_at DESC",
                    tuple(inp.skus),
                ).fetchall()
            ]
        elif inp.status_filter == "STAGED":
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM items WHERE status = 'STAGED' ORDER BY updated_at DESC"
                ).fetchall()
            ]
        else:
            rows = [
                dict(r)
                for r in conn.execute("SELECT * FROM items ORDER BY updated_at DESC").fetchall()
            ]

        for row in rows:
            row["aspects"] = row.get("aspects_json") or "{}"
            row["media_urls"] = row.get("media_urls_json") or "[]"

        csv_text = generate_seller_hub_draft_csv(rows, location_label=inp.location_label)
        md_lines = [
            f"### Seller Hub Reports Draft CSV Export (`{len(rows)}` draft row(s))",
            "- **Format**: `FX_LISTING` (`Action=Draft`, valid 75 days in Seller Hub -> Listings -> Drafts)",
            "- **Line Endings**: Strict Windows `CRLF` (`\\r\\n`) with sanitized single-line cells (`BAF.Error.5` safe)",
            "- **Upload Path**: Seller Hub -> Reports -> Uploads -> Upload template",
        ]
        return format_tool_response(
            {
                "ok": True,
                "tool": "ebay_export_draft_csv",
                "row_count": len(rows),
                "crlf_verified": "\r\n" in csv_text,
                "csv_text": csv_text,
                "preview_card_md": "\n".join(md_lines),
            }
        )
    finally:
        conn.close()
