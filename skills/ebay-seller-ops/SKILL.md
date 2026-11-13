---
name: ebay-seller-ops
description: Multimodal listing intake, anti-INAD QA etiquette, 2026 unit economics (PLG + 139-DIM), Seller Hub CRLF draft export, and progressive C2C-to-Pro store workflows.
metadata:
  hermes:
    blueprint:
      cron_jobs:
        - id: ebay-stale-cassini-check
          schedule: "30 6 * * *"
          mode: "--no-agent"
          script: "scripts/stale_listing_diagnostics.py"
          description: "Daily 90-180d Cassini decay check: distinguishes in-place aspect fixes vs 5% gentle pruning."
        - id: ebay-offer-96h-unpaid-triage
          schedule: "0 */2 * * *"
          mode: "--no-agent"
          script: "scripts/offer_and_unpaid_triage.py"
          description: "Every 2h PLG-adjusted Best Offer floor check + 96-hour unpaid SellerAccept alert."
        - id: ebay-buyer-qa-inad-shield
          schedule: "*/30 * * * *"
          mode: "--no-agent"
          script: "scripts/buyer_qa_inbox.py"
          description: "Every 30m facts-only measurement reply drafter + 3-business-day INAD landmark check."
        - id: ebay-evening-haul-sprint
          schedule: "30 19 * * *"
          mode: "--no-agent"
          script: "scripts/haul_micro_sprint.py"
          description: "7:30 PM 3-item death-pile micro-sprint & eBay Balance break-even nudge ([SILENT] when clear)."
---

# eBay Seller Operations Companion (`hermes-ebay-ops`)

Follow these operational rules whenever assisting a user with eBay listing drafts, mobile Magical Listing audits, 2026 net-margin pricing, Best Offers, migration imports, or order fulfillment.

## 1. Progressive Lite-to-Pro Etiquette
- **Casual C2C Default ("Lite" Mode)**: Never ask casual sellers upfront for a SKU, Cost of Goods Sold (COGS), minimum Best Offer floor price, or warehouse storage bin code. Let `ebay_stage_listing` auto-mint a hidden `HRM-YYYYMMDD-XXXX` SKU and show estimated net payout.
- **Seller Hub Draft CSV Dual-Path**: Casual accounts not opted into Business Policies cannot create editable Seller Hub Web UI drafts via REST. Offer `ebay_export_draft_csv` whenever a casual seller wants to review/edit staged items inside **Seller Hub -> Listings -> Drafts** (exports strict Windows `CRLF` `\r\n` `Action=Draft` CSV with newline-sanitized descriptions so `BAF.Error.5` never occurs).
- **Automatic Reseller Promotion ("Pro" Mode)**: Whenever the seller mentions `cogs_cents`, `bin_code`, or `floor_price_cents`, pass them directly to `ebay_stage_listing` or `ebay_audit_active_listing`. Doing so automatically unlocks `ebay_triage_offers`, `ebay_lookup_bin`, `ebay_import_migration_csv`, and `ebay_diagnose_stale`.

## 2. "Snap-and-Speak" Multimodal Intake & Anti-Hallucination Rules
- **Ordered Photo Sequence**: Encourage sellers to capture photos in order: (1) Clean Hero Front, (2) Back/Profile, (3) Brand/Size/Care Tag or Serial Plate, (4) Ruler Lay-Flat Measurements, and (5) Macro Close-Ups of Every Flaw.
- **Never Invent Item Specifics**: Only populate brand, material, size, OEM MPN, or measurements explicitly visible in photos/tags or stated in `seller_notes`. Delegate category-specific compliance to the matching playbook (`ebay-apparel-footwear`, `ebay-motors-fitment`, `ebay-vintage-collectibles`, or `ebay-cards-coins-media`).
- **Zero AI Marketing Fluff**: Never emit filler phrases (*"elevate your wardrobe"*, *"stunning"*, *"must-have"*, *"look no further"*). Keep titles factual and `<= 80` characters.

## 3. 2026 Net-Margin, PLG Attribution, & July 2026 `139`-DIM Guardrails
- **Promoted Listings General (Jan 13, 2026 Rule)**: Any buyer sale within 30 days of *any* buyer clicking a PLG ad incurs the ad fee. Pass `plg_ad_rate_bps` (e.g., `500` for `5.0%`) when modeling promoted inventory, and pass `buyer_tax_rate_bps=700` (`~7%` US sales tax) when computing worst-case Final Value Fee basis.
- **July 12, 2026 USPS `139` DIM Divisor**: Parcels exceeding `1,728 cu in` (`1 cu ft`) bill at `ceil((L * W * H) / 139)` lbs rather than scale weight, and any side `> 48 in` triggers carrier nonstandard length surcharges. Always pass `package_dims` on coats, boots, electronics, and auto parts.
- **Active vs. Realized Comps**: `ebay_check_comps` queries live eBay Browse supply (`q <= 100` chars or `image_b64` visual match) and cross-checks realized `SOLD` rows in the local SQLite ledger. Pair with `@ebay/npm-public-api-mcp` when read-only developer spec discovery is needed alongside Hermes OAuth writes.

## 4. Two-Step Human Confirmation Gate & Account Safety
- **Hard Invariant**: Never call `ebay_confirm_action(decision="APPROVE")` in the same turn that stages an action or without explicit human approval in chat—including `BULK_PUBLISH` (`<= 25 SKUs/call`) batches imported from shuttered `inkFrog` or `myFitment` CSVs. Every `APPROVE` call opens Hermes's own approval prompt for the seller, and the write runs only if they approve it there; tell them the prompt is coming, and never retry or look for another route after a denial. Unattended runs (cron) cannot approve.
- **2026 `SellerAccept` & 96-Hour Unpaid Rule**: Never re-action `SellerAccept` Best Offers awaiting buyer checkout; flag unpaid `SellerAccept` holds only once they cross `96 hours` (`4 days`).
