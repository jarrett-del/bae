"""Dual-Path Seller Hub Reports Draft CSV Generator (Windows CRLF) and
Orphaned SaaS CSV Importer (inkFrog, myFitment, Seller Hub Reports).

Solves:
- Report §5.1: Seller Hub Drafts API limitation for casual accounts without Business Policies
  by generating a strict Windows CRLF (`\\r\\n`) `FX_LISTING` CSV (`Action=Draft`) with sanitized
  single-line descriptions so Seller Hub never fails with `BAF.Error.5`.
- Report §3.4 & §6: One-step migration from shuttered inkFrog (June 1, 2026) and myFitment
  (Sept 30, 2026) CSV exports into clean local drafts ready for `25 SKUs/call` bulk publishing.
"""

from __future__ import annotations

import csv
import io
import json
import re
from decimal import Decimal
from typing import Any

from .models import ConditionEnum, normalize_condition

SELLER_HUB_DRAFT_ACTION_HEADER = "*Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)"

SELLER_HUB_DRAFT_COLUMNS: tuple[str, ...] = (
    SELLER_HUB_DRAFT_ACTION_HEADER,
    "CustomLabel",
    "*Category",
    "*Title",
    "*ConditionID",
    "*C:Brand",
    "*C:Size",
    "*C:Size Type",
    "*C:Department",
    "PicURL",
    "*Description",
    "*Format",
    "*Duration",
    "*StartPrice",
    "*Quantity",
    "*Location",
    "ShippingType",
    "ShippingService-1:Option",
    "ShippingService-1:Cost",
    "DispatchTimeMax",
    "ReturnsAcceptedOption",
)

INKFROG_BOILERPLATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<!--\s*inkfrog.*?-->", re.IGNORECASE | re.DOTALL),
    re.compile(r"<(?:script|style|iframe|link)[^>]*>.*?</(?:script|style|iframe)>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<link[^>]*inkfrog[^>]*>", re.IGNORECASE),
    re.compile(r"<img[^>]*inkfrog\.com[^>]*>", re.IGNORECASE),
    re.compile(r"<div[^>]*id=['\"]?inkfrog[^>]*>.*?</div>", re.IGNORECASE | re.DOTALL),
    re.compile(r"Powered by inkFrog[\w\s.!]*", re.IGNORECASE),
)


def sanitize_csv_cell(raw: Any, *, preserve_html_breaks: bool = False) -> str:
    """Strip raw CR/LF line breaks inside cells to prevent Seller Hub Reports `BAF.Error.5`."""
    if raw is None:
        return ""
    text = str(raw)
    if preserve_html_breaks:
        text = text.replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>")
    else:
        text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return re.sub(r" {2,}", " ", text).strip()


def strip_inkfrog_template_html(raw_html: str) -> str:
    """Clean broken inkFrog gallery/template wrappers while preserving factual seller copy."""
    cleaned = raw_html or ""
    for pat in INKFROG_BOILERPLATE_PATTERNS:
        cleaned = pat.sub(" ", cleaned)
    cleaned = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</p>|</li>|</div>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in cleaned.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def generate_seller_hub_draft_csv(
    items: list[dict[str, Any]],
    *,
    location_label: str = "United States",
) -> str:
    """Render a Windows CRLF (`\\r\\n`) Seller Hub Reports Draft CSV (`Action=Draft`, valid 75 days)."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(SELLER_HUB_DRAFT_COLUMNS)

    for item in items:
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

        def _asp(key: str, default: str = "") -> str:
            v = aspects.get(key, default)
            if isinstance(v, list):
                return ", ".join(str(x) for x in v)
            return str(v) if v is not None else default

        raw_media = item.get("media_urls")
        if isinstance(raw_media, str):
            try:
                media_urls = json.loads(raw_media)
            except Exception:
                media_urls = []
        elif isinstance(raw_media, list):
            media_urls = raw_media
        else:
            media_urls = []

        cond = normalize_condition(item.get("condition_enum") or item.get("condition") or "USED_GOOD")
        cat_id = sanitize_csv_cell(item.get("category_id") or "57988")
        cond_id = cond.browse_condition_id(cat_id)

        sku = sanitize_csv_cell(item.get("sku") or "")
        bin_code = sanitize_csv_cell(item.get("bin_code") or "")
        custom_label = f"{bin_code}-{sku}"[:50] if (bin_code and not sku.startswith(bin_code)) else sku

        price_cents = max(1, int(item.get("list_price_cents") or 100))
        ship_cents = max(0, int(item.get("shipping_est_cents") or 0))
        price_str = f"{Decimal(price_cents) / Decimal(100):.2f}"
        ship_str = f"{Decimal(ship_cents) / Decimal(100):.2f}"

        base_desc = str(item.get("description") or item.get("flaws_disclosure") or item.get("title") or "")
        flaws = str(item.get("flaws_disclosure") or "")
        if flaws and flaws not in base_desc:
            base_desc = f"{base_desc}\nCondition Disclosure: {flaws}"
        safe_desc = sanitize_csv_cell(base_desc, preserve_html_breaks=True)

        pic_joined = "|".join(
            sanitize_csv_cell(u) for u in media_urls if str(u).startswith(("https://", "http://"))
        )

        row = [
            "Draft",
            custom_label,
            cat_id,
            sanitize_csv_cell(str(item.get("title") or "Pre-Owned Item")[:80]),
            cond_id,
            sanitize_csv_cell(_asp("Brand", "Unbranded")),
            sanitize_csv_cell(_asp("Size") or _asp("US Shoe Size")),
            sanitize_csv_cell(_asp("Size Type", "Regular")),
            sanitize_csv_cell(_asp("Department", "Unisex Adults")),
            pic_joined,
            safe_desc,
            "FixedPrice",
            "GTC",
            price_str,
            "1",
            sanitize_csv_cell(location_label),
            "Flat",
            "USPSGroundAdvantage",
            ship_str,
            "3",
            "ReturnsAccepted",
        ]
        writer.writerow(row)

    return buf.getvalue()


def parse_migration_csv(
    csv_text: str,
    *,
    source_hint: str = "auto",
    default_condition: ConditionEnum = ConditionEnum.USED_GOOD,
) -> tuple[str, list[dict[str, Any]]]:
    """Parse orphaned inkFrog, myFitment, or Seller Hub CSV exports into normalized draft dicts."""
    normalized_text = csv_text.lstrip("\ufeff").strip()
    reader = csv.DictReader(io.StringIO(normalized_text))
    headers = [h.strip() for h in (reader.fieldnames or []) if h]
    header_lower = {h.lower(): h for h in headers}

    detected = source_hint.lower()
    if detected == "auto":
        if any("fitment" in h or "make" == h or "partnumber" in h.replace("_", "") for h in header_lower):
            detected = "myfitment"
        elif any("inkfrog" in h or "template" in h for h in header_lower) or "inkfrog" in normalized_text.lower():
            detected = "inkfrog"
        else:
            detected = "seller_hub"

    grouped_by_key: dict[str, dict[str, Any]] = {}

    for idx, raw_row in enumerate(reader, start=1):
        row = {(k or "").strip(): (v or "").strip() for k, v in raw_row.items() if k}
        lower_map = {k.lower(): v for k, v in row.items()}

        sku_raw = (
            lower_map.get("customlabel")
            or lower_map.get("custom_label")
            or lower_map.get("sku")
            or lower_map.get("partnumber")
            or lower_map.get("part_number")
            or f"MIG-{idx:04d}"
        )
        safe_sku = re.sub(r"[^A-Za-z0-9_-]+", "-", sku_raw).strip("-")[:50] or f"MIG-{idx:04d}"
        if len(safe_sku) < 3:
            safe_sku = f"MIG-{safe_sku}"

        mpn = lower_map.get("mpn") or lower_map.get("partnumber") or lower_map.get("part_number") or ""
        raw_title = (
            lower_map.get("*title")
            or lower_map.get("title")
            or lower_map.get("item_title")
            or lower_map.get("description_short")
            or f"{mpn} Replacement Part".strip()
        )
        if detected == "myfitment" and mpn and mpn.lower() not in raw_title.lower():
            raw_title = f"OEM {mpn} {raw_title}".strip()
        clean_title = sanitize_csv_cell(raw_title)[:80] or "Migrated eBay Listing"

        raw_desc = (
            lower_map.get("*description")
            or lower_map.get("description")
            or lower_map.get("html_description")
            or lower_map.get("notes")
            or clean_title
        )
        clean_desc = strip_inkfrog_template_html(raw_desc) if detected == "inkfrog" else strip_inkfrog_template_html(raw_desc)

        price_str = (
            lower_map.get("*startprice")
            or lower_map.get("startprice")
            or lower_map.get("price")
            or lower_map.get("buy_it_now_price")
            or "29.99"
        )
        price_clean = re.sub(r"[^0-9.]", "", price_str) or "29.99"
        price_cents = max(100, int(round(float(price_clean) * 100)))

        cogs_str = lower_map.get("cost") or lower_map.get("cogs") or ""
        cogs_cents = (
            int(round(float(re.sub(r"[^0-9.]", "", cogs_str)) * 100))
            if re.sub(r"[^0-9.]", "", cogs_str)
            else None
        )
        bin_code = lower_map.get("bin") or lower_map.get("bin_code") or lower_map.get("location_bin") or None

        cat_id = (
            lower_map.get("*category")
            or lower_map.get("category")
            or lower_map.get("category_id")
            or ("6030" if detected == "myfitment" else "57988")
        )
        cond_raw = lower_map.get("*conditionid") or lower_map.get("condition") or default_condition.value
        try:
            cond = normalize_condition(cond_raw)
        except Exception:
            cond = default_condition

        pic_raw = lower_map.get("picurl") or lower_map.get("image_urls") or lower_map.get("photo_url") or ""
        media_urls = [
            u.strip()
            for u in re.split(r"[|,;\s]+", pic_raw)
            if u.strip().startswith(("https://", "http://")) and "inkfrog.com" not in u.lower()
        ]

        entry = grouped_by_key.get(safe_sku)
        if entry is None:
            aspects: dict[str, str] = {}
            if lower_map.get("*c:brand") or lower_map.get("brand"):
                aspects["Brand"] = lower_map.get("*c:brand") or lower_map["brand"]
            if mpn:
                aspects["MPN"] = mpn
            if lower_map.get("*c:size") or lower_map.get("size"):
                aspects["Size"] = lower_map.get("*c:size") or lower_map["size"]
            entry = {
                "sku": safe_sku,
                "title": clean_title,
                "description": clean_desc or clean_title,
                "category_id": cat_id,
                "condition": cond,
                "list_price_cents": price_cents,
                "cogs_cents": cogs_cents,
                "bin_code": bin_code,
                "aspects": aspects,
                "media_urls": media_urls or ["https://i.ebayimg.com/images/g/bae-migrated/s-l1600.jpg"],
                "fitment_rows": [],
            }
            grouped_by_key[safe_sku] = entry

        # Collect multi-row Year/Make/Model/Trim/Engine compatibility from myFitment CSVs
        yr = lower_map.get("year")
        mk = lower_map.get("make")
        md = lower_map.get("model")
        if yr and mk and md:
            trim = lower_map.get("trim") or lower_map.get("submodel") or ""
            eng = lower_map.get("engine") or ""
            fit_line = " ".join(x for x in (yr, mk, md, trim, eng) if x)
            if fit_line not in entry["fitment_rows"]:
                entry["fitment_rows"].append(fit_line)

    results: list[dict[str, Any]] = []
    for item in list(grouped_by_key.values())[:25]:
        fit_rows = item.pop("fitment_rows")
        if fit_rows:
            item["aspects"]["Compatible Vehicles"] = "; ".join(fit_rows[:8])
            item["description"] = (
                f"{item['description']}\nVerified Vehicle Fitment ({len(fit_rows)}): " + ", ".join(fit_rows[:12])
            )
        results.append(item)

    return detected, results
