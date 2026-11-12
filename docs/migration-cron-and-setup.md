# CSV Migration, Seller Hub Drafts & Scheduled Checks

This guide covers importing CSVs from discontinued listing tools (`inkFrog` and `myFitment`), exporting Windows `CRLF` Seller Hub Drafts, running zero-token (`--no-agent`) scheduled checks, and managing eBay developer API quotas.

---

## 1. Dual Publishing Path: Live API vs. Seller Hub `CRLF` Draft CSV

Not every seller wants an agent publishing directly to eBay on day one. Vendere supports two complementary paths from the exact same staged SQLite record:

### Path A — Live Two-Step API Publish (`ebay_confirm_action`)
- Stages a `PUBLISH_LISTING` token with SHA-256 verification.
- On approval, publishes via **Sell Inventory REST** (opted-in Business Policy accounts) or **Trading XML `AddFixedPriceItem`** (casual C2C accounts).

### Path B — Zero-Risk Seller Hub Draft CSV (`ebay_export_draft_csv` / `↓ CRLF Draft CSV`)
- Generates an eBay File Exchange / Seller Hub Reports upload file (`*Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)` with `Action=Draft`).
- **Strict Windows `CRLF` (`\r\n`) Line Endings & Single-Line Cell Sanitization**: Replaces embedded `\r` and `\n` inside multi-line descriptions and notes with clean `<br>` / single-line spacing so Seller Hub never rejects uploads with `BAF.Error.5` column-shift errors.
- Imported drafts sit safely in **Seller Hub -> Listings -> Drafts for up to 75 days**, letting sellers attach mobile photos or review final shipping options in eBay's native UI.

---

## 2. Orphaned `inkFrog` & `myFitment` CSV Migration (`<=25 SKUs/call`)

With `inkFrog` and `myFitment` shuttered, resellers and auto-parts stores often hold thousands of orphaned CSV rows. Use `ebay_import_migration_csv` or open **`03 · Listing Bench -> ⇄ Import inkFrog / myFitment CSV`**:

1. **Auto-Detects Format**:
   - **`inkFrog` Exports**: Strips legacy `<div class="inkfrog-...">` HTML wrappers and `<style>` blocks down to clean mobile-friendly copy while preserving SKU (`CustomLabel`), price, condition, category, and gallery URLs.
   - **`myFitment` Exports**: Aggregates multi-row Year / Make / Model / Trim / Engine rows sharing the same `PartNumber` / `SKU` into a single `items` record with structured `compatibility` vehicles, front-loads OEM `MPN`, and runs Motors compliance checks.
   - **Standard Seller Hub CSVs**: Imports active or draft spreadsheets directly into Vendere's local ledger.
2. **Stages a Single `BULK_PUBLISH` Token**:
   - Zero live eBay writes happen during CSV import.
   - Vendere stages one cryptographic `BULK_PUBLISH` token in `01 · Morning Ops`. When approved, `LiveEbayAdapter.bulk_publish_listings(...)` chunks payloads into eBay's **`<=25 SKUs/call`** `bulk_create_or_replace_inventory_item` + `bulk_create_offer` batches.

---

## 3. 90+ Day Stale-Listing Checks & Haul Break-Even Tracking

Fixed-price listings that sit unsold past 90–180 days tend to lose visibility in eBay search, yet ending and relisting everything at once risks **Duplicate Listings Policy** flags or losing watchers unnecessarily.

`ebay_diagnose_stale` (and the Morning Ops strip) splits aged listings (`>=90 days`) into two safe tracks:
- **`IN_PLACE_ASPECT_UPGRADE`**: Aged listings missing recommended leaf-category specifics (`Material`, `Pattern`, `Measurements`, `MPN`) are upgraded in place via `REVISE_LISTING` to unlock Cassini aspect filters while keeping existing item history and watchers.
- **`END_AND_RECREATE_5PCT` (Gentle 5% Daily Cap)**: Aged listings that already have complete specifics are queued for sequential end-and-recreate at a `-5%` price refresh, capped at **5% of active store inventory per day** so active titles never overlap.

Alongside stale triage, the **Haul & Death-Pile Velocity** bar tracks batch COGS recovery and turns unlisted piles into **9-minute, 3-item evening micro-sprints**, showing the exact eBay Balance unlocked per sprint.

---

## 4. Zero-LLM-Token (`--no-agent`) Scheduled Cron Blueprints

Running an LLM every 30 minutes just to check an empty inbox burns tokens and creates notification fatigue. Vendere bundles four pure-Python `--no-agent` scripts in `scripts/` (also testable interactively via **`Run Check`** in `04 · Setup & Logs`):

| Blueprint ID | Recommended Cron | Script | Behavior |
| :--- | :--- | :--- | :--- |
| `ebay-stale-cassini-check` | `30 6 * * *` (Daily 06:30) | `scripts/stale_listing_diagnostics.py` | Prints `[SILENT]` if 0 active items exceed 90 days; otherwise outputs a concise `IN_PLACE_ASPECT_UPGRADE` vs. `END_AND_RECREATE_5PCT` digest. |
| `ebay-offer-96h-unpaid-triage` | `0 */2 * * *` (Every 2h) | `scripts/offer_and_unpaid_triage.py` | Prints `[SILENT]` when no `Active` offers or `>=96h` unpaid `SellerAccept` holds exist; otherwise summarizes net payout after 5% PLG vs. floor price. |
| `ebay-buyer-qa-inad-shield` | `*/30 * * * *` (Every 30m) | `scripts/buyer_qa_inbox.py` | Prints `[SILENT]` when no buyer question is queued; otherwise drafts a `<2,000`-char facts-only measurement reply + 3-day INAD landmark check. |
| `ebay-evening-haul-sprint` | `30 19 * * *` (Daily 19:30) | `scripts/haul_micro_sprint.py` | Nudges a 3-item (`~9 min`) death-pile micro-sprint while Haul break-even `> $0`; prints `[SILENT]` once all hauls are 100% listed. |

---

## 5. OpenAPI Spec MCP Pairing & Dec 31, 2026 Partner Milestones

### Optional Read-Only `@ebay/npm-public-api-mcp` Pairing

Vendere handles all authenticated seller execution and two-step write gating. Developers extending category or schema workflows can pair eBay's official read-only OpenAPI specification MCP server in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  ebay_specs:
    command: "npx"
    args: ["-y", "@ebay/npm-public-api-mcp"]
```

### API Quota & 2026 eBay Partner Checklist (`04 · Setup & Logs`)

- **`5,000 calls/day` Basic Access Quota**: Vendere caches `get_item_aspects_for_category` responses for **7 days** in `cache.db` and truncates Browse `q` strings to `<=100` characters so everyday store operations stay well inside default limits.
- **Free Application Growth Check**: Stores scaling above `5,000` daily Taxonomy/Browse calls can request a free **Application Growth Check** in the eBay Developer Portal (`developer.ebay.com`) by demonstrating local aspect caching and human write-gating.
- **December 31, 2026 Partner Program Cutoff**: Multi-tenant SaaS or commercial partner integrations seeking elevated eBay Partner Program tiers should submit applications ahead of the **December 31, 2026** program window tracked in `04 · Setup & Logs`.
