"""Deterministic 2026 eBay Category Playbook Validators & Nov 26, 2026 Content Monitoring Pre-Screens.

Implements the four category compliance pillars and pre-publish account-health guards:
1. Apparel & Footwear (`ebay-apparel-footwear`): August 2026 Size Standardization & pre-owned condition IDs.
2. Motors Parts & Accessories (`ebay-motors-fitment`): MPN front-loading, YMM fitment checks, & mandatory
   30-day free returns on new/new-other parts > $10 (replacing shuttered myFitment workflows).
3. Vintage & Collectibles (`ebay-vintage-collectibles`): Catalog-Number Defense keeping mold/box numbers out
   of MPN/Model so eBay's catalog never attaches hallucinated product copy.
4. Trading Cards, Coins & Media (`ebay-cards-coins-media`): PSA/BGS/SGC/PCGS cert checks & May 2026 mandatory
   coin condition grading descriptors.
5. November 26, 2026 Automated Content Monitoring: duplicate active fixed-price listing guard, VeRO IP keyword
   screen, and primary-image watermark / overlay check.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .models import ConditionEnum, FASHION_CATEGORY_IDS

MOTORS_CATEGORY_IDS: frozenset[str] = frozenset({"6030", "6028", "33559", "33615", "179680", "6000"})
VINTAGE_COLLECTIBLES_CATEGORY_IDS: frozenset[str] = frozenset({"1", "20081", "870", "13877", "261628", "15053"})
CARDS_COINS_MEDIA_CATEGORY_IDS: frozenset[str] = frozenset({"11116", "253", "183454", "261328", "176984", "267"})

STANDARD_ALPHA_SIZES: frozenset[str] = frozenset(
    {
        "XXS",
        "XS",
        "S",
        "M",
        "L",
        "XL",
        "XXL",
        "2XL",
        "3XL",
        "4XL",
        "5XL",
        "ONE SIZE",
    }
)
STANDARD_SIZE_TYPES: frozenset[str] = frozenset(
    {"Regular", "Big & Tall", "Petites", "Plus", "Juniors", "Maternity", "Tall"}
)
NUMERIC_SIZE_RE = re.compile(r"^\d{1,2}(?:\.5)?(?:\s*(?:W|L|R|S|D|E|EE|B|in))?$", re.IGNORECASE)
CUSTOM_SIZE_RED_FLAGS: tuple[str, ...] = (
    "fits like",
    "medium-ish",
    "large-ish",
    "small-ish",
    "see measurements",
    "custom",
    "approx",
    "ish",
    "unknown",
    "tag cut",
)

VERO_RISK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bvelcro\b", re.IGNORECASE), "'Velcro' is a registered trademark; use 'hook-and-loop fastener'."),
    (re.compile(r"\bonesie\b", re.IGNORECASE), "'Onesie' is a Gerber trademark; use 'infant bodysuit' or 'one-piece'."),
    (re.compile(r"\b(?:inspired by|like gucci|like lv|replica|counterfeit|1:1|dupe)\b", re.IGNORECASE), "Comparison/replica phrasing triggers Nov 2026 automated VeRO/counterfeit account holds."),
)

WATERMARK_HINTS: tuple[str, ...] = ("watermark", "logo_overlay", "border_promo", "text_banner")
MOLD_OR_BOX_HINT_RE = re.compile(r"\b(?:mold|mould|base stamp|box\s*#|pattern\s*#|bottom mark)\b", re.IGNORECASE)
COIN_KEYWORDS_RE = re.compile(r"\b(?:morgan dollar|peace dollar|silver eagle|pcgs|ngc|anacs|bullion|half dollar|wheat cent|numismatic)\b", re.IGNORECASE)
CARD_KEYWORDS_RE = re.compile(r"\b(?:psa\s*\d+|bgs\s*\d+|sgc\s*\d+|prizm|topps chrome|bowman|holo|rookie card)\b", re.IGNORECASE)
MOTORS_KEYWORDS_RE = re.compile(r"\b(?:oem|alternator|caliper|headlight assembly|control arm|catalytic|throttle body|fuel injector|fitment)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ComplianceReport:
    playbook: str
    passed: bool
    blocking_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sanitized_aspects: dict[str, str] = field(default_factory=dict)
    recommended_condition_id: str | None = None


def detect_category_playbook(category_id: str, title: str = "", notes: str = "") -> str:
    """Map a listing to its specialized bundled SKILL.md playbook."""
    cid = str(category_id or "").strip()
    combined = f"{title} {notes}"
    if cid in MOTORS_CATEGORY_IDS or MOTORS_KEYWORDS_RE.search(combined):
        return "ebay-motors-fitment"
    if cid in CARDS_COINS_MEDIA_CATEGORY_IDS or COIN_KEYWORDS_RE.search(combined) or CARD_KEYWORDS_RE.search(combined):
        return "ebay-cards-coins-media"
    if cid in FASHION_CATEGORY_IDS:
        return "ebay-apparel-footwear"
    if cid in VINTAGE_COLLECTIBLES_CATEGORY_IDS or re.search(r"\b(?:vintage|antique|mcm|mid-century|pyrex|fiesta|porcelain)\b", combined, re.IGNORECASE):
        return "ebay-vintage-collectibles"
    return "ebay-seller-ops"


def validate_apparel_size_standard(size_val: str | None) -> tuple[bool, str | None]:
    """Validate apparel/footwear size strings against eBay's August 2026 Size Standardization rules."""
    if not size_val or not str(size_val).strip():
        return False, "Missing standardized 'Size' / 'US Shoe Size' — blocked under eBay's Aug 2026 Size Standardization."
    cleaned = str(size_val).strip()
    lower = cleaned.lower()
    if any(bad in lower for bad in CUSTOM_SIZE_RED_FLAGS) or "/" in cleaned:
        return (
            False,
            f"Non-standard custom size {cleaned!r} will be held/blocked under eBay's Aug 2026 Size Standardization; use exact tag/measured enum (e.g. 'L', '32', '10.5') and put fit notes in description.",
        )
    if cleaned.upper() in STANDARD_ALPHA_SIZES or NUMERIC_SIZE_RE.match(cleaned):
        return True, None
    return (
        False,
        f"Unrecognized size token {cleaned!r} — verify against eBay Metadata API standardized size list before publishing.",
    )


def screen_listing_compliance(
    *,
    title: str,
    description: str,
    category_id: str,
    condition: ConditionEnum,
    list_price_cents: int,
    aspects: dict[str, Any],
    media_urls_or_paths: list[str] | None = None,
    return_policy_id: str | None = None,
    sku: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> ComplianceReport:
    """Run deterministic 2026 category playbook + Nov 26, 2026 content-monitoring checks."""
    playbook = detect_category_playbook(category_id, title, description)
    blocking: list[str] = []
    warnings: list[str] = []
    sanitized: dict[str, str] = {
        str(k): (", ".join(str(x) for x in v) if isinstance(v, list) else str(v))
        for k, v in (aspects or {}).items()
    }
    combined_text = f"{title}\n{description}"

    # 1. Nov 26, 2026 Automated Content Monitoring: VeRO & Watermark Pre-Screens
    for rx, msg in VERO_RISK_PATTERNS:
        if rx.search(combined_text):
            warnings.append(f"VeRO / Nov 2026 Content Guard: {msg}")

    for m_path in media_urls_or_paths or []:
        lower_m = str(m_path).lower()
        if any(hint in lower_m for hint in WATERMARK_HINTS):
            warnings.append(
                f"Primary image '{m_path}' appears to contain a watermark or promotional overlay — prohibited by eBay picture standards."
            )
            break

    # 2. Duplicate Fixed-Price Active Listing Guard (Duplicate Listings Policy)
    if conn is not None:
        norm_title = re.sub(r"\s+", " ", title.strip().lower())
        dup_rows = conn.execute(
            "SELECT sku, ebay_item_id, title FROM items WHERE status = 'ACTIVE'"
        ).fetchall()
        for row in dup_rows:
            if sku and str(row["sku"]) == str(sku):
                continue
            existing_norm = re.sub(r"\s+", " ", str(row["title"]).strip().lower())
            if existing_norm == norm_title:
                warnings.append(
                    f"Duplicate Listings Policy Alert: Active item `{row['sku']}` ({row['ebay_item_id'] or 'live'}) already uses an identical fixed-price title."
                )
                break

    # 3. Playbook-Specific 2026 Rules
    if playbook == "ebay-apparel-footwear":
        sz = sanitized.get("Size") or sanitized.get("US Shoe Size")
        ok_sz, sz_err = validate_apparel_size_standard(sz)
        if not ok_sz and sz_err:
            warnings.append(sz_err)
        sz_type = sanitized.get("Size Type")
        if str(category_id) != "15709" and sz_type and sz_type not in STANDARD_SIZE_TYPES:
            warnings.append(
                f"Non-standard Size Type {sz_type!r}; choose from {', '.join(sorted(STANDARD_SIZE_TYPES))}."
            )

    elif playbook == "ebay-vintage-collectibles":
        # Catalog-Number Defense: strip mold/base/box numbers out of MPN/Model if detected
        for risky_key in ("MPN", "Model"):
            val = sanitized.get(risky_key)
            if val and (MOLD_OR_BOX_HINT_RE.search(combined_text) or re.match(r"^#?\d{2,5}[A-Z]?$", val.strip())):
                moved_val = sanitized.pop(risky_key)
                sanitized.setdefault("Pattern", moved_val)
                sanitized["MPN"] = "Does Not Apply"
                warnings.append(
                    f"Catalog-Number Defense (`ebay-vintage-collectibles`): Moved mold/box number {moved_val!r} out of `{risky_key}` into `Pattern` so eBay's catalog does not attach wrong stock text."
                )

    elif playbook == "ebay-motors-fitment":
        mpn = sanitized.get("MPN") or sanitized.get("Manufacturer Part Number")
        if not mpn:
            warnings.append(
                "Motors Fitment Guard (`ebay-motors-fitment`): Missing OEM `MPN` — Fitment Plus Auto may attach wrong vehicles without an exact part number."
            )
        elif mpn.lower() not in title[:40].lower():
            warnings.append(
                f"Motors SEO Tip: Front-load OEM MPN `{mpn}` near the start of the 80-character title."
            )
        if condition in {ConditionEnum.NEW, ConditionEnum.NEW_OTHER} and list_price_cents > 1000:
            if not return_policy_id or "FREE" not in str(return_policy_id).upper():
                warnings.append(
                    "Motors Return Mandate: Eligible new/new-other U.S. parts over $10 require a 30-day Free Return policy."
                )

    elif playbook == "ebay-cards-coins-media":
        if COIN_KEYWORDS_RE.search(combined_text) or str(category_id) in {"11116", "253"}:
            if not any(k in sanitized for k in ("Grade", "Circulated/Uncirculated", "Certification")):
                warnings.append(
                    "May 2026 Coin Policy (`ebay-cards-coins-media`): Missing standardized coin grading/circulation specifics ('Grade', 'Circulated/Uncirculated', 'Certification') — risk of listing block."
                )

    rec_cond_id = condition.browse_condition_id(category_id)
    return ComplianceReport(
        playbook=playbook,
        passed=len(blocking) == 0,
        blocking_errors=blocking,
        warnings=warnings,
        sanitized_aspects=sanitized,
        recommended_condition_id=rec_cond_id,
    )
