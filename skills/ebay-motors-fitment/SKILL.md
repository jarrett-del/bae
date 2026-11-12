---
name: ebay-motors-fitment
description: eBay Motors Parts & Accessories playbook replacing shuttered myFitment workflows with OEM MPN front-loading, Year/Make/Model compatibility checks, and mandatory 30-day free returns over $10.
---

# eBay Motors Parts & Accessories Playbook (`ebay-motors-fitment`)

Apply these rules whenever staging, migrating, or auditing automotive, motorcycle, or powersports parts (`6030`, `6028`, `33559`, `33615`, `179680`, `6000`).

## 1. OEM `MPN` Extraction & Title Front-Loading
- Stampings on castings, Denso/Bosch labels, and OEM boxes are the primary search anchor in eBay Motors.
- Always populate exact `MPN` (Manufacturer Part Number) and `OE/OEM Part Number` when stamped on the part, and front-load `"OEM <MPN>"` within the first 40 characters of the 80-character title.
- **Never guess vehicle sub-trims or engine displacements**: eBay's *Fitment Plus Auto* can attach incompatible trims if `MPN` is missing or ambiguous, leaving the seller liable for return shipping on "Doesn't Fit" claims.

## 2. Replacing Shuttered `myFitment` (Sept 30, 2026)
- Sellers migrating off `myFitment` can pass multi-row Year/Make/Model/Trim/Engine CSV exports directly to `ebay_import_migration_csv(source_hint="myfitment")`.
- Verify connector pin counts, left/right vehicle placement (`Driver Left LH` vs `Passenger Right RH`), and bracket tabs in close-up photos so buyers visually confirm fitment before checkout.

## 3. Mandatory 30-Day Free Returns (`> $10` New Parts) & Dimensional Weight
- Eligible `NEW` and `NEW_OTHER` U.S. Parts & Accessories listings priced above `$10.00` require a **30-day Free Return** business policy. Verify `return_policy_id` before publishing.
- Bumpers, headlights, exhaust pipes, and trim pieces routinely exceed `1,728 cu in` or `48 inches` in length—always pass `package_dims` to `ebay_stage_listing` so July 2026 USPS `139` DIM weight and `>48 in` oversize surcharges are priced in upfront.
