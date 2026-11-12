"""Shared economics calculations, note extraction, INAD boilerplate detection,
and structured JSON/Markdown response envelopes for Hermes tools.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..config import get_settings

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
) -> EconomicsBreakdown:
    settings = get_settings()
    fee_bps = estimated_fee_bps if estimated_fee_bps is not None else settings.default_estimated_fee_bps
    fixed_cents = fixed_fee_cents if fixed_fee_cents is not None else settings.per_order_fixed_fee_cents

    gross_fee_basis = max(0, list_price_cents + shipping_est_cents)
    variable_fee = int(round((gross_fee_basis * fee_bps) / 10_000))
    total_fees = variable_fee + fixed_cents
    net_payout = list_price_cents - total_fees

    if cogs_cents is None:
        return EconomicsBreakdown(
            list_price_cents=list_price_cents,
            shipping_est_cents=shipping_est_cents,
            estimated_fee_bps=fee_bps,
            fixed_fee_cents=fixed_cents,
            estimated_fees_cents=total_fees,
            estimated_net_payout_cents=net_payout,
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


def format_tool_response(payload: dict[str, Any]) -> str:
    """Serialize structured tool result as JSON string (Hermes tool handler contract)."""
    return json.dumps(payload, indent=2, ensure_ascii=False)
