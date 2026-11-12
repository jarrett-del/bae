---
name: ebay-vintage-collectibles
description: Vintage, antiques, pottery, glass, and collectibles playbook enforcing Catalog-Number Defense (keeping mold/box numbers out of MPN/Model) and search_by_image visual comping.
---

# Vintage & Collectibles Playbook (`ebay-vintage-collectibles`)

Apply these rules whenever handling vintage hardgoods, mid-century glass/pottery, estate collectibles, cameras, or discontinued toys (`1`, `20081`, `870`, `13877`, `261628`, `31388`).

## 1. Catalog-Number Defense (Preventing eBay Product Catalog Corruption)
- Vintage ceramics, Pyrex/Fiesta bowls, brassware, and board-game boxes often carry 3–5 digit **mold numbers**, **kiln stamps**, or **box insert codes** on the base.
- **Hard Rule**: Never place mold numbers, patent dates, or box insert codes into `MPN`, `Model`, or `UPC`. When a short numeric code lands in `MPN`, eBay's catalog engine frequently matches an unrelated modern SKU and silently injects wrong stock specifications into the listing—triggering automatic INAD losses.
- Place base markings in `Pattern` or `Production Technique` and set `MPN` to `"Does Not Apply"` unless a verified manufacturer SKU exists.

## 2. Visual Comping (`search_by_image`) & Era Honesty
- Unbranded estate pieces rarely match keyword searches cleanly. Pass `image_b64` to `ebay_check_comps` (`POST /buy/browse/v1/item_summary/search_by_image`) to anchor pricing against visually identical active listings.
- Only use `"Vintage"` when the piece is 20+ years old and `"Antique"` when 100+ years old. Disclose crazing, flea-bite rim chips, brass verdigris, or shutter-speed sluggishness verbatim without polishing away collector context.
