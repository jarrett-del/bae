"""Shared economics calculations, note extraction, INAD boilerplate detection,
and structured JSON/Markdown response envelopes for Hermes tools.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from ..config import get_settings
from ..models import FASHION_CATEGORY_IDS, PackageDimensions

AI_BOILERPLATE_PHRASES: tuple[str, ...] = (
    "elevate your wardrobe",
    "stunning",
    "timeless",
    "must-have",
    "must have",
    "look no further",
    "effortless style",
    "adventure-ready",
    "turn heads",
    "perfect addition",
    "holy grail",
    "rare gem",
)

FLAW_KEYWORDS: tuple[str, ...] = (
    "stain",
    "hole",
    "tear",
    "ripped",
    "snag",
    "pilling",
    "scuff",
    "scratch",
    "fade",
    "faded",
    "fading",
    "heel drag",
    "missing button",
    "broken zipper",
    "crack",
    "cracked",
    "chip",
    "chipped",
    "discoloration",
    "wear on",
    "light wear",
    "spot on",
    "mark on",
    "fraying",
)

KNOWN_BRANDS: tuple[str, ...] = (
    "Patagonia",
    "Levi's",
    "Levis",
    "Nike",
    "New Balance",
    "Arc'teryx",
    "Arcteryx",
    "Carhartt",
    "Lululemon",
    "Pendleton",
    "Filson",
    "Sony",
    "Canon",
    "Nikon",
    "Apple",
    "Nintendo",
)


@dataclass(frozen=True)
class EconomicsBreakdown:
    list_price_cents: int
    shipping_est_cents: int
    estimated_fee_bps: int
    fixed_fee_cents: int
    estimated_fees_cents: int
    estimated_net_payout_cents: int
    cogs_cents: int | None = None
    net_profit_cents: int | None = None
    margin_pct: float | None = None
    plg_ad_rate_bps: int = 0
    plg_ad_fee_cents: int = 0
    buyer_tax_rate_bps: int = 0
    taxed_total_cents: int = 0
    very_high_inad_risk_cents: int = 0


STOCKED_BOX_INVENTORY: tuple[dict[str, Any], ...] = (
    {"code": "POLY-10X13", "label": "Poly Mailer 12×10×2", "length_in": 12.0, "width_in": 10.0, "height_in": 2.0},
    {"code": "BOX-SHOE", "label": "Shoe / Denim Box 14×10×6", "length_in": 14.0, "width_in": 10.0, "height_in": 6.0},
    {"code": "BOX-MED", "label": "Medium Parcel 16×12×8 (<1 cu ft)", "length_in": 16.0, "width_in": 12.0, "height_in": 8.0},
    {"code": "BOX-PARKA", "label": "Winter Coat Box 18×14×8 (139-DIM)", "length_in": 18.0, "width_in": 14.0, "height_in": 8.0},
    {"code": "BOX-LONG", "label": "Trim / Club Tube 50×8×8 (>48 in)", "length_in": 50.0, "width_in": 8.0, "height_in": 8.0},
)


@dataclass(frozen=True)
class DimensionalShippingCheck:
    cubic_inches: float
    actual_lbs: int
    dim_lbs_2026: int
    legacy_166_lbs: int
    billable_lbs: int
    dim_triggered: bool
    oversize_triggered: bool
    recommended_box_code: str | None
    summary_note: str
    warnings: list[str]
    policy_lock_note: str


def format_usd(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    abs_cents = abs(int(cents))
    return f"{sign}${abs_cents / 100:.2f}"


def compute_economics(
    *,
    list_price_cents: int,
    shipping_est_cents: int = 0,
    estimated_fee_bps: int | None = None,
    fixed_fee_cents: int | None = None,
    cogs_cents: int | None = None,
    plg_ad_rate_bps: int = 0,
    buyer_tax_rate_bps: int = 0,
    very_high_inad_surcharge_bps: int = 0,
) -> EconomicsBreakdown:
    """Compute eBay 2026 seller proceeds, including optional Jan 2026 PLG 30-day attribution,
    estimated buyer sales-tax FVF basis (~7%), and Service Metrics 'Very High' INAD (+5%) exposure.
    """
    settings = get_settings()
    fee_bps = estimated_fee_bps if estimated_fee_bps is not None else settings.default_estimated_fee_bps
    fixed_cents = fixed_fee_cents if fixed_fee_cents is not None else settings.per_order_fixed_fee_cents

    subtotal = max(0, list_price_cents + shipping_est_cents)
    tax_cents = int(round((subtotal * max(0, buyer_tax_rate_bps)) / 10_000)) if buyer_tax_rate_bps > 0 else 0
    taxed_total = subtotal + tax_cents

    effective_fee_bps = fee_bps + max(0, very_high_inad_surcharge_bps)
    variable_fee = int(round((taxed_total * effective_fee_bps) / 10_000))
    plg_fee = int(round((taxed_total * max(0, plg_ad_rate_bps)) / 10_000)) if plg_ad_rate_bps > 0 else 0
    inad_risk_preview = int(round((taxed_total * 500) / 10_000))

    total_fees = variable_fee + fixed_cents + plg_fee
    net_payout = list_price_cents - total_fees

    if cogs_cents is None:
        return EconomicsBreakdown(
            list_price_cents=list_price_cents,
            shipping_est_cents=shipping_est_cents,
            estimated_fee_bps=fee_bps,
            fixed_fee_cents=fixed_cents,
            estimated_fees_cents=total_fees,
            estimated_net_payout_cents=net_payout,
            plg_ad_rate_bps=plg_ad_rate_bps,
            plg_ad_fee_cents=plg_fee,
            buyer_tax_rate_bps=buyer_tax_rate_bps,
            taxed_total_cents=taxed_total,
            very_high_inad_risk_cents=inad_risk_preview,
        )

    net_profit = net_payout - cogs_cents
    margin = round((net_profit / list_price_cents) * 100.0, 1) if list_price_cents > 0 else 0.0
    return EconomicsBreakdown(
        list_price_cents=list_price_cents,
        shipping_est_cents=shipping_est_cents,
        estimated_fee_bps=fee_bps,
        fixed_fee_cents=fixed_cents,
        estimated_fees_cents=total_fees,
        estimated_net_payout_cents=net_payout,
        cogs_cents=cogs_cents,
        net_profit_cents=net_profit,
        margin_pct=margin,
        plg_ad_rate_bps=plg_ad_rate_bps,
        plg_ad_fee_cents=plg_fee,
        buyer_tax_rate_bps=buyer_tax_rate_bps,
        taxed_total_cents=taxed_total,
        very_high_inad_risk_cents=inad_risk_preview,
    )


@dataclass(frozen=True)
class ExtractedNotes:
    title: str
    aspects: dict[str, str]
    measurements: list[str]
    flaws_disclosure: str | None
    clean_description: str


def extract_structured_notes(
    seller_notes: str,
    title_hint: str | None = None,
) -> ExtractedNotes:
    """Extract factual measurements, explicit aspects, and flaws without inventing specs."""
    combined = f"{title_hint or ''} {seller_notes}".strip()
    aspects: dict[str, str] = {}
    measurements: list[str] = []

    # Brand match (only if explicitly mentioned)
    for brand in KNOWN_BRANDS:
        if re.search(rf"\b{re.escape(brand)}\b", combined, flags=re.IGNORECASE):
            normalized_brand = "Levi's" if brand.lower() == "levis" else ("Arc'teryx" if brand.lower() == "arcteryx" else brand)
            aspects["Brand"] = normalized_brand
            break

    # Department match
    if re.search(r"\b(men'?s|mens)\b", combined, flags=re.IGNORECASE):
        aspects["Department"] = "Men"
        aspects["Size Type"] = "Regular"
    elif re.search(r"\b(women'?s|womens)\b", combined, flags=re.IGNORECASE):
        aspects["Department"] = "Women"
        aspects["Size Type"] = "Regular"
    elif re.search(r"\bunisex\b", combined, flags=re.IGNORECASE):
        aspects["Department"] = "Unisex Adults"
        aspects["Size Type"] = "Regular"

    # Waist x Inseam e.g. 32x30
    wx_match = re.search(r"\b(\d{2})\s*[xX]\s*(\d{2})\b", combined)
    if wx_match:
        waist_val, inseam_val = wx_match.group(1), wx_match.group(2)
        aspects.setdefault("Size", waist_val)
        aspects["Waist Size"] = f"{waist_val} in"
        aspects["Inseam"] = f"{inseam_val} in"
        measurements.append(f"Waist: {waist_val} in")
        measurements.append(f"Inseam: {inseam_val} in")

    # Pit-to-pit / Chest measurement
    p2p_match = re.search(
        r"(?:pit[\s\-]*to[\s\-]*pit|p2p|chest)\s*[:=]?\s*(\d{1,2}(?:\.\d+)?)\s*(?:\"|in|inch|inches)?",
        combined,
        flags=re.IGNORECASE,
    )
    if p2p_match:
        val = p2p_match.group(1)
        measurements.append(f"Pit-to-Pit / Chest: {val} in")
        aspects.setdefault("Chest Size", f"{val} in")

    # Garment length
    len_match = re.search(
        r"(?:length|top[\s\-]*to[\s\-]*bottom)\s*[:=]?\s*(\d{1,2}(?:\.\d+)?)\s*(?:\"|in|inch|inches)?",
        combined,
        flags=re.IGNORECASE,
    )
    if len_match:
        val = len_match.group(1)
        measurements.append(f"Length: {val} in")
        aspects.setdefault("Garment Length", f"{val} in")

    # Explicit Inseam if not already captured
    inseam_match = re.search(
        r"inseam\s*[:=]?\s*(\d{1,2}(?:\.\d+)?)\s*(?:\"|in|inch|inches)?",
        combined,
        flags=re.IGNORECASE,
    )
    if inseam_match and "Inseam" not in aspects:
        val = inseam_match.group(1)
        aspects["Inseam"] = f"{val} in"
        measurements.append(f"Inseam: {val} in")

    # Explicit letter/shoe size
    shoe_match = re.search(r"\b(?:us\s*)?size\s*(\d{1,2}(?:\.5)?)\b", combined, flags=re.IGNORECASE)
    if shoe_match:
        aspects.setdefault("US Shoe Size", shoe_match.group(1))
        aspects.setdefault("Size", shoe_match.group(1))
    else:
        alpha_size = re.search(
            r"\b(?:size\s*[:=]?\s*)?(XXS|XS|XXL|2XL|3XL|XL|SMALL|MEDIUM|LARGE)\b|\bsize\s*[:=]?\s*(S|M|L)\b",
            combined,
            flags=re.IGNORECASE,
        )
        if alpha_size:
            raw_sz = (alpha_size.group(1) or alpha_size.group(2) or "").upper()
            sz_map = {"SMALL": "S", "MEDIUM": "M", "LARGE": "L"}
            aspects.setdefault("Size", sz_map.get(raw_sz, raw_sz))

    # Garment type hints
    if re.search(r"\b(fleece|jacket|parka|anorak)\b", combined, flags=re.IGNORECASE):
        aspects.setdefault("Type", "Jacket")
    elif re.search(r"\bcoat\b", combined, flags=re.IGNORECASE):
        aspects.setdefault("Type", "Coat")
    elif re.search(r"\bvest\b", combined, flags=re.IGNORECASE):
        aspects.setdefault("Type", "Vest")
    elif re.search(r"\b(jeans|501)\b", combined, flags=re.IGNORECASE):
        aspects.setdefault("Style", "Straight")

    # Motors OEM MPN & Cards/Coins certification / grade extraction
    mpn_match = re.search(
        r"\b(?:mpn|oem|part\s*#?)\s*[:=]?\s*([A-Za-z0-9][A-Za-z0-9\-]{3,24})\b",
        combined,
        flags=re.IGNORECASE,
    )
    if mpn_match:
        aspects.setdefault("MPN", mpn_match.group(1).upper())

    cert_match = re.search(r"\b(PSA|BGS|SGC|PCGS|NGC|ANACS)\s*(MS\d{2}|PR\d{2}|PF\d{2}|AU\d{2}|XF\d{2}|\d{1,2}(?:\.5)?)\b", combined, flags=re.IGNORECASE)
    if cert_match:
        aspects.setdefault("Certification", cert_match.group(1).upper())
        aspects.setdefault("Grade", cert_match.group(2).upper())

    # Flaw extraction (verbatim clauses containing flaw indicators)
    clauses = [c.strip() for c in re.split(r"[.;\n]+", seller_notes) if c.strip()]
    flaw_clauses = [
        clause
        for clause in clauses
        if any(kw in clause.lower() for kw in FLAW_KEYWORDS)
    ]
    flaws_disclosure = "; ".join(flaw_clauses) if flaw_clauses else None

    # Strip marketing fluff
    sanitized_notes = seller_notes.strip()
    for phrase in AI_BOILERPLATE_PHRASES:
        sanitized_notes = re.sub(re.escape(phrase), "", sanitized_notes, flags=re.IGNORECASE)
    sanitized_notes = re.sub(r"\s{2,}", " ", sanitized_notes).strip(" ,!-")

    # Build concise <=80 char title
    if title_hint and title_hint.strip():
        raw_title = title_hint.strip()
    else:
        first_clause = clauses[0] if clauses else sanitized_notes
        raw_title = re.sub(r"\s+", " ", first_clause).strip()
    for phrase in AI_BOILERPLATE_PHRASES:
        raw_title = re.sub(re.escape(phrase), "", raw_title, flags=re.IGNORECASE)
    title = re.sub(r"\s{2,}", " ", raw_title).strip(" ,!-")[:80] or "Pre-Owned Item"

    desc_lines = [sanitized_notes or title]
    if measurements:
        desc_lines.append("Measurements: " + ", ".join(measurements) + ".")
    if flaws_disclosure:
        desc_lines.append(f"Condition Disclosure: {flaws_disclosure}.")
    clean_description = "\n".join(desc_lines)

    return ExtractedNotes(
        title=title,
        aspects=aspects,
        measurements=measurements,
        flaws_disclosure=flaws_disclosure,
        clean_description=clean_description,
    )


def detect_boilerplate_and_inad_risks(
    *,
    description: str,
    aspects: dict[str, str],
    category_id: str,
) -> tuple[list[str], list[str], str]:
    """Return (boilerplate_hits, inad_warnings, cleaned_description)."""
    lower_desc = description.lower()
    hits = [phrase for phrase in AI_BOILERPLATE_PHRASES if phrase in lower_desc]
    warnings: list[str] = []
    if hits:
        warnings.append(
            f"Generic AI marketing boilerplate detected ({', '.join(hits[:4])}); invites buyer remorse/INAD disputes."
        )

    has_numeric_measurement = bool(
        re.search(r"\b\d{1,2}(?:\.\d+)?\s*(?:\"|in\b|inch|cm\b)", description, flags=re.IGNORECASE)
        or any(k in aspects for k in ("Chest Size", "Inseam", "Waist Size", "Garment Length"))
    )
    if category_id in {"57988", "11483", "15709"} and not has_numeric_measurement:
        warnings.append(
            "Missing concrete ruler measurements (e.g. pit-to-pit, length, or inseam); primary driver of apparel INAD returns."
        )

    cleaned = description
    for phrase in AI_BOILERPLATE_PHRASES:
        cleaned = re.sub(re.escape(phrase), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[!]+", ".", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.-")
    if not cleaned:
        cleaned = "Pre-owned item. Please review photos and item specifics for exact condition and sizing."

    return hits, warnings, cleaned


def shipping_weight_sanity_note(package_weight_oz: float | None) -> str:
    if package_weight_oz is None:
        return "Weight not provided — measure packed weight before purchasing label to avoid carrier adjustment fees."
    if package_weight_oz <= 16.0:
        return f"{package_weight_oz:.1f} oz — eligible for USPS Ground Advantage under-1-lb tier."
    if package_weight_oz <= 70.0 * 16.0:
        lbs = package_weight_oz / 16.0
        return f"{package_weight_oz:.1f} oz ({lbs:.2f} lb) — ships cubic/dimensional Ground Advantage or Priority."
    return f"{package_weight_oz:.1f} oz — exceeds USPS 70 lb limit; verify UPS/FedEx Ground."


def compute_dimensional_shipping(
    package_weight_oz: float | None,
    package_dims: dict[str, Any] | PackageDimensions | None = None,
    *,
    list_price_cents: int = 0,
    category_id: str = "",
    fulfillment_policy_id: str | None = None,
) -> DimensionalShippingCheck:
    """Evaluate post-July 12, 2026 USPS 139 DIM-weight divisor, >48 in oversize surcharges,
    stocked box fit, and $200-$500 Ship With eBay fashion protection limits.
    """
    actual_lbs = max(1, math.ceil(float(package_weight_oz) / 16.0)) if package_weight_oz else 1
    warnings: list[str] = []
    policy_note = (
        f"Locked to explicit fulfillmentPolicyId `{fulfillment_policy_id}`."
        if fulfillment_policy_id
        else "Inline USPSGroundAdvantage fallback (no silent Business Policy substitution)."
    )

    if str(category_id) in FASHION_CATEGORY_IDS and 20_000 <= int(list_price_cents) <= 50_000:
        warnings.append(
            "Ship With eBay $200-$500 Fashion Cap: Orders between $200 and $500 fall below eBay's $500 Authenticity "
            "Guarantee threshold while hitting standard carrier claim caps — purchase label via Pirate Ship / Shippo "
            "with third-party insurance + signature confirmation."
        )

    if package_dims is None:
        return DimensionalShippingCheck(
            cubic_inches=0.0,
            actual_lbs=actual_lbs,
            dim_lbs_2026=0,
            legacy_166_lbs=0,
            billable_lbs=actual_lbs,
            dim_triggered=False,
            oversize_triggered=False,
            recommended_box_code=None,
            summary_note=shipping_weight_sanity_note(package_weight_oz),
            warnings=warnings,
            policy_lock_note=policy_note,
        )

    if isinstance(package_dims, PackageDimensions):
        l_in, w_in, h_in = package_dims.length_in, package_dims.width_in, package_dims.height_in
    else:
        l_in = float(package_dims.get("length_in") or 0.0)
        w_in = float(package_dims.get("width_in") or 0.0)
        h_in = float(package_dims.get("height_in") or 0.0)

    cubic = round(l_in * w_in * h_in, 1)
    dim_lbs_2026 = math.ceil(cubic / 139.0) if cubic > 1728.0 else 0
    legacy_166 = math.ceil(cubic / 166.0) if cubic > 1728.0 else 0
    billable_lbs = max(actual_lbs, dim_lbs_2026)
    dim_triggered = cubic > 1728.0 and dim_lbs_2026 > actual_lbs
    oversize_triggered = max(l_in, w_in, h_in) > 48.0

    # Match smallest stocked bench box
    target_sorted = sorted([l_in, w_in, h_in], reverse=True)
    rec_box: str | None = None
    for box in STOCKED_BOX_INVENTORY:
        box_sorted = sorted([box["length_in"], box["width_in"], box["height_in"]], reverse=True)
        if all(b >= t for b, t in zip(box_sorted, target_sorted)):
            rec_box = str(box["code"])
            break

    if dim_triggered:
        warnings.append(
            f"July 2026 USPS 139-DIM Alert: {l_in:g}×{w_in:g}×{h_in:g} in = {cubic:,.0f} cu in (>1,728 cu in) "
            f"bills at {dim_lbs_2026} lb (vs {actual_lbs} lb scale weight & {legacy_166} lb pre-July-2026 166 divisor). "
            "Cut box height by 1.5–2 in to drop under 1,728 cu in and avoid post-delivery APV clawbacks."
        )
    if oversize_triggered:
        warnings.append(
            f"Oversize Length Alert (>48 in): Longest side {max(l_in, w_in, h_in):g} in triggers carrier nonstandard "
            "length surcharge ($18+)."
        )

    summary = (
        f"{l_in:g}×{w_in:g}×{h_in:g} in ({cubic:,.0f} cu in) · Scale {actual_lbs} lb -> "
        f"Billable {billable_lbs} lb"
        + (" [2026 139-DIM ACTIVE]" if dim_triggered else " [Under 1 cu ft — Scale Weight]")
    )
    return DimensionalShippingCheck(
        cubic_inches=cubic,
        actual_lbs=actual_lbs,
        dim_lbs_2026=dim_lbs_2026,
        legacy_166_lbs=legacy_166,
        billable_lbs=billable_lbs,
        dim_triggered=dim_triggered,
        oversize_triggered=oversize_triggered,
        recommended_box_code=rec_box,
        summary_note=summary,
        warnings=warnings,
        policy_lock_note=policy_note,
    )


MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "ebay_stage_listing",
        "ebay_audit_active_listing",
        "ebay_confirm_action",
        "ebay_triage_offers",
        "ebay_import_migration_csv",
        "ebay_diagnose_stale",
    }
)


def announce_change(plugin_name: str | None = None) -> None:
    """Broadcast a real-time desk refresh event to open Hermes Webapp / Desktop windows."""
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event

        broadcast_plugin_event(plugin_name or get_settings().plugin_name, "changed", {})
    except Exception:
        pass


def format_tool_response(payload: dict[str, Any]) -> str:
    """Serialize structured tool result as JSON string (Hermes tool handler contract)."""
    if payload.get("ok") is True and payload.get("tool") in MUTATING_TOOLS:
        announce_change()
    return json.dumps(payload, indent=2, ensure_ascii=False)
