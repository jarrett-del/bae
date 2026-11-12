# 2026 Fee Math, Shipping & Category Rules

`bae` builds eBay's **2026 fee, shipping, condition, and category rules** directly into Python helpers (`tools/__init__.py` and `compliance.py`) paired with five bundled `SKILL.md` guides.

---

## 1. 2026 Net-Margin & USPS `139`-DIM Shipping Engine

### July 12, 2026 USPS `139` Dimensional Weight Divisor

Effective **July 12, 2026**, USPS tightened its dimensional-weight divisor from `166` to **`139`** for parcels exceeding **1 cubic foot (`1,728 cu in`)**:

$$\text{DIM Weight (lbs)} = \left\lceil \frac{L \times W \times H}{139} \right\rceil \quad \text{when } L \times W \times H > 1{,}728\text{ cu in}$$

`compute_dimensional_shipping(...)` in `tools/__init__.py`:
- Compares actual scale weight (`ceil(weight_oz / 16)`) against `ceil((L * W * H) / 139)` whenever cubic volume exceeds `1,728 cu in`, billing the higher weight so winter coats, boots, and vintage kitchenware never suffer surprise post-delivery APV postage clawbacks.
- Emits an explicit **Oversize Length Warning** when `max(L, W, H) > 48 in`.
- Recommends the smallest stocked box preset (`POLY-10X13`, `BOX-SHOE`, `BOX-MED`, `BOX-PARKA`, `BOX-LONG`) and calculates how many inches to trim box height to drop back under `1,728 cu in`.

### January 13, 2026 PLG Attribution, Buyer-Tax FVF Basis & INAD Surcharge Preview

`compute_economics(...)` models the real deductions that erode reseller margins in 2026:
- **Final Value Fee (FVF) Basis**: Computed on `list_price + shipping_est + (~7% estimated buyer sales tax)` plus the fixed per-order fee (`$0.30` for orders `<= $10.00`, `$0.40` above `$10.00`).
- **Promoted Listings General (`PLG`) 30-Day Attribution (Jan 13, 2026)**: Since any buyer click on a `PLG` ad within 30 days triggers the ad fee even if another shopper or organic visit buys the item, `bae` previews `0% Organic`, `5% PLG`, and `8% PLG` net payouts side by side.
- **`"Very High"` Service Metrics INAD (`+5%`) Preview**: Displays the exact `-5%` surcharge penalty so sellers see why accurate ruler measurements and flaw disclosures matter financially.
- **\$200–\$500 Ship With eBay Fashion Protection Guard**: Warns fashion sellers in the `$200–$500` band (below the `$500+` Authenticity Guarantee threshold) to purchase labels through **Ship With eBay** so Seller Protections cover false return claims.
- **`fulfillmentPolicyId` Lock**: Staging a listing binds the explicit `fulfillmentPolicyId` from `seller_profile` so eBay cannot silently override shipping methods at publish time.

---

## 2. Five Bundled `SKILL.md` Playbooks

`bae` registers one core skill plus four leaf-category playbooks via `ctx.register_skill(...)`, backed by pre-publish checks in `compliance.py`:

| Skill Name | Target Categories | Key 2026 Rules & Pre-Publish Guards |
| :--- | :--- | :--- |
| **`ebay-seller-ops`** | All categories (Core) | Photo-and-notes listing intake, Magical Listing filler cleanup, 80-char search title structure, CRLF Draft CSV export, and two-step approval workflow. |
| **`ebay-apparel-footwear`** | Clothing, Shoes & Accessories (`57988`, `11483`, `15709`, `11450`, `15724`, etc.) | **Aug 2026 Size Standardization** (blocks free-text `"Fits like Medium"` in `Size`), maps expanded pre-owned fashion Condition IDs (`2990` Pre-Owned Excellent, `3000` Pre-Owned Good, `3010` Pre-Owned Fair), requires flat-lay ruler measurements, and enforces the `$200–$500` Ship With eBay warning. |
| **`ebay-motors-fitment`** | eBay Motors Parts & Accessories (`6028`, `6030`, `33559`–`33743`, etc.) | Replaces shuttered `myFitment`: front-loads exact OEM `MPN` in the first 40 title characters, formats Year/Make/Model (`YMM`) compatibility tables, warns against unverified Fitment Plus Auto guesses, and checks **mandatory 30-day Free Returns** on eligible new/new-other U.S. parts `> $10`. |
| **`ebay-vintage-collectibles`** | Collectibles, Pottery/Glass, Vintage Electronics (`1`, `870`, `15230`, `293`, etc.) | **Catalog-Number Defense**: detects mold, base, or box numbers (e.g., Pyrex `#443`) placed inside `MPN` or `Model`, automatically moves them into `Pattern`, and sets `MPN = "Does Not Apply"` so eBay's catalog never overwrites vintage listings with wrong stock text. Uses `search_by_image` visual comps. |
| **`ebay-cards-coins-media`** | Sports/TCG Cards, Coins & Bullion, Media (`261328`, `183454`, `11116`, `253`, etc.) | Enforces **May 2026 Coin Condition & Grading Policy** (`Grade`, `Circulated/Uncirculated`, `Certification`), checks PSA/BGS/SGC/PCGS/NGC slab cert callouts, and validates eBay Standard Envelope (`<= $20` single / `<= $50` combined, `<= 3 oz`) eligibility. |

---

## 3. November 26, 2026 Automated Content-Monitoring Pre-Screens

Every call to `ebay_stage_listing` and `ebay_audit_active_listing` runs `screen_listing_compliance(...)` before staging an approval token:

1. **Duplicate Active Fixed-Price Title Check**: Compares normalized titles against all `ACTIVE` items in `data.db` so sellers never trigger eBay's Duplicate Listings Policy by publishing two identical fixed-price listings.
2. **VeRO High-Risk Keyword Screen**: Flags trademark traps (such as writing `"Velcro"` instead of `"hook-and-loop fastener"` or `"Onesie"` instead of `"infant bodysuit"`) before eBay's automated content filters suppress the listing.
3. **Primary Image Watermark / Border Check**: Warns if primary media filenames or metadata indicate watermarks, text overlays, or promotional borders prohibited on eBay gallery images.
4. **March 2026 `SellerAccept` Best Offer Lifecycle**: Recognizes that accepting a buyer's Best Offer sets status to `SellerAccept` while keeping the item **live for immediate checkout by other buyers** until paid — preventing premature inventory delisting while flagging `>=96h` unpaid holds.
