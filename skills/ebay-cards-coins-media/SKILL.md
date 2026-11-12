---
name: ebay-cards-coins-media
description: Trading cards, numismatic coins, bullion, and media playbook enforcing PSA/BGS/SGC/PCGS slab cert verification, May 2026 mandatory coin condition grading, and eBay Standard Envelope thresholds.
---

# Trading Cards, Coins & Media Playbook (`ebay-cards-coins-media`)

Apply these rules whenever staging or auditing sports/TCG cards, coins & paper money, vinyl, video games, or books (`183454`, `261328`, `11116`, `253`, `176984`, `267`).

## 1. May 2026 Coin & Bullion Mandatory Condition Grading
- Under eBay's May 2026 Coins & Paper Money update, listings lacking structured grading/circulation specifics risk immediate listing blocks.
- Always populate `Circulated/Uncirculated`, `Certification` (`PCGS`, `NGC`, `ANACS`, or `Uncertified`), and `Grade` (e.g., `MS 64`, `PR 69`, or `Ungraded`). Never estimate numerical Sheldon grades on raw uncertified coins—describe luster, rim dings, and cleaning hairlines factually and let high-res obverse/reverse macros speak for themselves.

## 2. Graded Slab & Raw Card Anti-INAD Rules
- For graded slabs (`PSA`, `BGS`, `SGC`, `CGC`), record exact `Certification Number`, `Grade`, `Parallel/Variety`, and `Card Number` directly from the flip label, and note any case scratches separately from card condition.
- For raw cards (`Condition ID 4000`), never claim `"PSA 10 contender"` or `"Gem Mint"` in titles or descriptions—use standardized card condition descriptors (`Near Mint or Better`, `Lightly Played`, `Moderately Played`) with four-corner front/back photos on a dark matte background.

## 3. eBay Standard Envelope & Media Mail Thresholds
- Single raw cards `<= $20` (up to `$50` multi-card orders) under `3 oz` and `<= 0.25 in` rigid thickness qualify for tracked **eBay Standard Envelope** (`~$0.69–$1.25`), preserving margin on `$3–$19` singles.
- Books, CDs, DVDs, and vinyl LP mailers qualify for **USPS Media Mail**, whereas video games, consoles, and comic books (containing advertising) **do not**—route games and comics via `USPSGroundAdvantage` to prevent postage-due seizures.
