"""Stale-Listing Cassini Diagnostics (90–180 Day Decay), 96-Hour Unpaid Commitment Checks,
and Zero-LLM-Token Buyer Q&A Factual Reply Builder.

Reconciles the two 2026 seller strategies documented in Report §4:
- Low-impression or missing-aspect active listings -> In-place `REVISE_LISTING` aspect/fitment upgrade
  (never blindly relist an item whose structured specifics are incomplete).
- Healthy-QA listings aged >= 90 days without conversion -> Sequential Gentle Pruning
  (end-and-recreate small daily cap with ~5% price drop, respecting Duplicate Listings Policy).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .compliance import screen_listing_compliance
from .db import parse_iso_utc
from .models import normalize_condition


def analyze_stale_listings(
    items: list[dict[str, Any]],
    *,
    min_age_days: int = 90,
    daily_prune_cap: int = 5,
    prune_discount_bps: int = 500,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Evaluate ACTIVE listings past `min_age_days` and classify upgrade vs. gentle-prune actions."""
    now_dt = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    diagnostics: list[dict[str, Any]] = []
    prune_assigned = 0

    for item in items:
        if str(item.get("status")) != "ACTIVE":
            continue
        listed_iso = item.get("listed_at") or item.get("created_at")
        if not listed_iso:
            continue
        try:
            age_days = int((now_dt - parse_iso_utc(str(listed_iso))).total_seconds() // 86400)
        except Exception:
            continue
        if age_days < min_age_days:
            continue

        raw_aspects = item.get("aspects")
        if isinstance(raw_aspects, str):
            try:
                aspects = json.loads(raw_aspects)
            except Exception:
                aspects = {}
        elif isinstance(raw_aspects, dict):
            aspects = raw_aspects
        else:
            aspects = {}

        cond = normalize_condition(item.get("condition_enum") or "USED_GOOD")
        price_cents = int(item.get("list_price_cents") or 100)
        comp_report = screen_listing_compliance(
            title=str(item.get("title") or ""),
            description=str(item.get("flaws_disclosure") or item.get("title") or ""),
            category_id=str(item.get("category_id") or ""),
            condition=cond,
            list_price_cents=price_cents,
            aspects=aspects,
            sku=str(item.get("sku") or ""),
        )
        qa_score = int(item.get("qa_score") or 90)
        needs_aspect_fix = bool(comp_report.warnings) or len(aspects) < 4 or qa_score < 85

        if needs_aspect_fix:
            strategy = "IN_PLACE_ASPECT_UPGRADE"
            suggested_price_cents = price_cents
            reason = (
                comp_report.warnings[0]
                if comp_report.warnings
                else "Incomplete left-rail item specifics starving Cassini impressions — fix in place before relisting."
            )
        else:
            prune_assigned += 1
            within_cap = prune_assigned <= daily_prune_cap
            strategy = "END_AND_RECREATE_5PCT" if within_cap else "QUEUED_NEXT_BATCH"
            discounted = int(round(price_cents * (10_000 - prune_discount_bps) / 10_000))
            floor_p = item.get("floor_price_cents")
            suggested_price_cents = max(int(floor_p), discounted) if floor_p else max(100, discounted)
            reason = (
                f"{age_days}d active with complete specifics — stage sequential end-and-recreate at -5% "
                "(Duplicate Listings Policy safe)."
                if within_cap
                else f"{age_days}d active — queued behind daily {daily_prune_cap}-item gentle-prune cap."
            )

        diagnostics.append(
            {
                "sku": item.get("sku"),
                "ebay_item_id": item.get("ebay_item_id"),
                "title": item.get("title"),
                "age_days": age_days,
                "playbook": comp_report.playbook,
                "strategy": strategy,
                "current_price_cents": price_cents,
                "suggested_price_cents": suggested_price_cents,
                "reason": reason,
            }
        )

    diagnostics.sort(key=lambda d: int(d["age_days"]), reverse=True)
    return diagnostics


def detect_unpaid_96h_offers(
    offers: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Flag SellerAccept unpaid Best Offer commitments stuck >= 96 hours (4 days) for clean relist."""
    now_dt = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    flagged: list[dict[str, Any]] = []
    for off in offers:
        status_str = str(off.get("status") or "")
        if status_str != "SellerAccept":
            continue
        ref_iso = off.get("accepted_at") or off.get("created_at")
        hours_open = 0.0
        if ref_iso:
            try:
                hours_open = (now_dt - parse_iso_utc(str(ref_iso))).total_seconds() / 3600.0
            except Exception:
                hours_open = 0.0
        if hours_open >= 96.0:
            flagged.append(
                {
                    "best_offer_id": off.get("best_offer_id"),
                    "ebay_item_id": off.get("ebay_item_id"),
                    "sku": off.get("sku"),
                    "hours_unpaid": round(hours_open, 1),
                    "recommendation": "CANCEL_UNPAID_AND_RELIST",
                }
            )
    return flagged


def draft_buyer_qa_reply(item: dict[str, Any], buyer_question: str) -> dict[str, Any]:
    """Build a deterministic facts-only reply (<2,000 chars) from local SQLite ledger data."""
    raw_aspects = item.get("aspects") or item.get("aspects_json") or {}
    if isinstance(raw_aspects, str):
        try:
            aspects = json.loads(raw_aspects)
        except Exception:
            aspects = {}
    else:
        aspects = dict(raw_aspects)

    q_lower = buyer_question.lower()
    is_damage_claim = any(
        kw in q_lower for kw in ("broken", "damaged", "not as described", "inad", "refund", "stain", "cracked")
    )

    spec_bits = []
    for key in ("Chest Size", "Garment Length", "Waist Size", "Inseam", "Size", "US Shoe Size", "MPN", "Brand"):
        if key in aspects:
            val = aspects[key]
            spec_bits.append(f"{key}: {', '.join(val) if isinstance(val, list) else val}")

    flaws = str(item.get("flaws_disclosure") or "No undisclosed wear noted at intake.")
    reply_lines = [
        f"Hi! Here are the recorded bench details for {item.get('title', 'this item')}:",
    ]
    if spec_bits:
        reply_lines.append("• Recorded Specs / Measurements: " + " | ".join(spec_bits))
    reply_lines.append(f"• Condition Notes: {flaws}")
    reply_lines.append("• Ships within 1–3 business days via tracked carrier.")
    reply_text = "\n".join(reply_lines)[:2000]

    return {
        "sku": item.get("sku"),
        "ebay_item_id": item.get("ebay_item_id"),
        "is_inad_claim_watch": is_damage_claim,
        "inad_defense_check": (
            "3-business-day resolution window active: compare buyer claim photo against intake frame landmarks, serial/tag shot, and lighting."
            if is_damage_claim
            else None
        ),
        "draft_reply": reply_text,
    }
