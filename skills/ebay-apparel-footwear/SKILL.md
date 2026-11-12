---
name: ebay-apparel-footwear
description: Apparel & footwear playbook enforcing August 2026 Size Standardization, 2990/3000/3010 pre-owned fashion conditions, ruler lay-flat measurements, and the $200-$500 Ship With eBay insurance rule.
---

# Apparel & Footwear Playbook (`ebay-apparel-footwear`)

Apply these rules whenever staging, auditing, or pricing clothing, denim, outerwear, handbags, or footwear (`57988`, `11483`, `15709`, `11450`, `63861`, `93427`).

## 1. August 2026 Size Standardization (Hard Compliance Rule)
- eBay phased out free-text custom `Size` strings across Clothing, Shoes & Accessories in August 2026.
- **Never** write custom phrases like `"Fits like Medium"`, `"Large-ish"`, `"32/33"`, or `"See measurements"` into `Size` or `US Shoe Size`—doing so causes listing holds or invisible left-rail filter drop-outs.
- Map `Size` strictly to standardized eBay Metadata tokens (`XXS`, `XS`, `S`, `M`, `L`, `XL`, `2XL`, `3XL`, or exact numeric waist/shoe size like `32` or `10.5`) and `Size Type` to `Regular`, `Big & Tall`, `Petites`, `Plus`, `Juniors`, or `Maternity`.
- When vintage shrinkage differs from the neck/waist tag, record the standardized measured size in `Size` and disclose both tag size and flat-lay ruler numbers in the description.

## 2. Expanded Pre-Owned Fashion Conditions (`2990` / `3000` / `3010`)
- Map pre-owned apparel accurately across eBay's expanded fashion condition IDs:
  - `USED_EXCELLENT` -> Condition ID `2990` (*Pre-owned - Excellent*: crisp fabric, zero stains/holes/odor)
  - `USED_GOOD` -> Condition ID `3000` (*Pre-owned - Good*: normal wash wear or minor disclosed flaw)
  - `USED_ACCEPTABLE` -> Condition ID `3010` (*Pre-owned - Fair*: visible distressing, repair, or workwear patina)

## 3. Anti-INAD Ruler Measurements & `$200–$500` Shipping Rule
- Every top/jacket requires **Pit-to-Pit (Chest)** and **Garment Length**; trousers/denim require **Flat Waist**, **Inseam**, and **Front Rise**; footwear requires **Outsole Heel-to-Toe** + insole wear photo.
- **Ship With eBay `$200–$500` Gap**: Fashion orders priced above `$500` route through eBay Authenticity Guarantee, whereas `$200–$500` designer pieces ship direct to buyer while exceeding standard carrier protection caps. Recommend **Pirate Ship** or **Shippo** with third-party parcel insurance and signature confirmation on `$200–$500` fashion sales.
